"""Validate the KLM-62D FREQUENCY CONTROL netlist against hand theory
(plan-style SPICE-vs-theory gate for this board):

  - IC41a/IC41b summing-amp DC gains follow the resistor matrix exactly
    (COARSE -0.122, FINE -0.0213, MOD1/2 -1, MOD R1/R2 +2.006 after the
    double inversion, -14.9V offset -> +0.677 V)
  - the temperament bus obeys the servo law  V(bus) = V(rail) - R423*Ic
    with Ic = I_ref * exp(-Vb/VT): the IC42b loop nulls the R417 current,
    so the bus sits 100 mV/uA below the Q402-stabilized rail
  - the law is exponential in the summed CV at ln2*52*VT ~= 0.932 V/oct
    (at the MOD1/MOD2 pins)
  - TOTAL TUNE scales I_ref as 14.9V/(620k + 470k*ttune)
  - LINEALITY ADJUST moves the Q402 base (VR402 wiper) over the ~26.5 mV
    divider span, shifting rail and bus 1:1
  - the regulated bus has near-zero output impedance until the load
    exceeds Q403's cutoff capacity |V(bus)|/10k + (100k/72k)*Ic

The junction-level re-reads behind this topology (feedback to the BUS,
R425 as zener bias, Q402 as NPN rail follower, D402 to the emitter rail)
are reflected in the netlist.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm62d-freqctl.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# --- hand theory (mirrors the netlist header and dsp/freqctl.dsp) ---
VT = 0.025865  # kT/q at ngspice's 27 C default
ISQ = 1e-14  # QC945/QA798 model IS
VAF = 100.0
BF798 = 250.0
BF945 = 300.0
OFFSET = 14.9 * 100e3 / 2.2e6  # R401 offset at IC41a out
KDIV = 100.0 / 5200.0  # R413/R416 base attenuator
R406P = 51e3 * 2.2e6 / (51e3 + 2.2e6)  # R406 || R428
RRAIL = 62e3 + 10e3  # R420 + R421
NCQ0 = 14.9 * 3.3 / 15.3  # Q402 collector node, unloaded
RNCQ = 12e3 * 3.3e3 / 15.3e3  # its Thevenin resistance


def o41b_hand(vmodr1=0.0, vmodr2=0.0):
    return float(np.clip(-(vmodr1 + vmodr2), -13.5, 13.5))


def o41a_hand(vcoarse=0.0, vfine=0.0, vmod1=0.0, vmod2=0.0, vmodr1=0.0, vmodr2=0.0):
    v = (
        OFFSET
        - 100e3 / 820e3 * vcoarse
        - 100e3 / 4.7e6 * vfine
        - vmod1
        - vmod2
        - 100e3 / R406P * o41b_hand(vmodr1, vmodr2)
    )
    return float(np.clip(v, -13.5, 13.5))


def iref_hand(ttune=0.5):
    return 14.9 / (620e3 + 470e3 * ttune)


def vw_hand(lin=0.5):
    """VR402 wiper voltage: the 1k pot parallels R426 100R between G3 and
    the R427 51k tap to -14.9V; the wiper divides that node linearly.
    (Q402's ~0.14 uA base current on the <500R wiper impedance is < 0.1 mV
    and neglected here and in the DSP.)"""
    rpar = 1.0 / (1.0 / 1000.0 + 1.0 / 100.0)
    va = -14.9 * rpar / (rpar + 51e3)
    return va * lin


def bus_hand(
    vcoarse=0.0, vfine=0.0, vmod1=0.0, vmod2=0.0, vmodr1=0.0, vmodr2=0.0, ttune=0.5, lin=0.5
):
    """Fixed-point solution of the regulated-bus law, with first-order
    Early (VAF=100 on both the A798 pair and Q402) and Q401-right base
    current (Ic/BF into the 98R base Thevenin) corrections; converges in
    ~3 iterations because everything enters logarithmically."""
    vb0 = KDIV * o41a_hand(vcoarse, vfine, vmod1, vmod2, vmodr1, vmodr2)
    iref = iref_hand(ttune)
    vebl = 0.55
    for _ in range(3):
        vebl = VT * np.log(iref / (ISQ * (1 + vebl / VAF)))
    vw = vw_hand(lin)
    rail = -0.55
    ic = iref
    for _ in range(4):
        ic = iref * np.exp(-vb0 / VT) * (1 + (vebl - rail) / VAF) / (1 + vebl / VAF)
        ic = (
            iref
            * np.exp(-(vb0 + ic / BF798 * 98.08) / VT)
            * (1 + (vebl - rail) / VAF)
            / (1 + vebl / VAF)
        )
        ie402 = ic * 100.0 / 72.0
        ic402 = ie402 * BF945 / (BF945 + 1.0)
        ncq = NCQ0 - RNCQ * ic402
        vbe402 = VT * np.log(ic402 / (ISQ * (1 + (ncq - rail) / VAF)))
        rail = vw - vbe402
    return {"bus": float(rail - 100e3 * ic), "rail": float(rail), "ic": float(ic)}


# --- deck runner ---
def run_freqctl(params: dict, control: str, outfile: str) -> np.ndarray:
    base = dict(
        vcoarse=0, vfine=0, vmod1=0, vmod2=0, vmodr1=0, vmodr2=0, ttune=0.5, lin=0.5, iload=0
    )
    base.update(params)
    pline = ".param " + " ".join(f"{k}={v:g}" for k, v in base.items())
    deck = NETLIST.read_text()
    deck = re.sub(r"^\.param .*$", pline, deck, count=1, flags=re.MULTILINE)
    deck = re.sub(r"(?s)\.control.*?\.endc", control, deck, count=1)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            ["ngspice", "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out = tmpdir / outfile
        if not out.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        return np.loadtxt(out)


OP_CONTROL = """.control
op
wrdata freqctl_op.txt v(bus) v(rail) v(o41a) v(o41b)
.endc"""


def run_op(params: dict) -> dict:
    d = run_freqctl(params, OP_CONTROL, "freqctl_op.txt")
    return {"bus": d[1], "rail": d[3], "o41a": d[5], "o41b": d[7]}


# --- tests ---
SUM_CASES = [
    dict(),
    dict(vcoarse=-14.9, vfine=14.9),
    dict(vmod1=2.0, vmod2=-1.0),
    dict(vmodr1=1.5, vmodr2=-0.5),
    dict(vcoarse=7.5, vmod1=-1.0, vmodr2=0.8),
]


@pytest.mark.parametrize(
    "case", SUM_CASES, ids=lambda c: "-".join(f"{k}{v}" for k, v in c.items()) or "zero"
)
def test_summing_amp_gains(case):
    """IC41a/IC41b outputs follow the resistor matrix (ideal-op-amp hand
    law; 0.5 mV covers the 1e6-gain op-amp model residual)."""
    d = run_op(case)
    assert d["o41b"] == pytest.approx(
        o41b_hand(case.get("vmodr1", 0), case.get("vmodr2", 0)), abs=5e-4
    )
    assert d["o41a"] == pytest.approx(o41a_hand(**case), abs=5e-4)


BUS_CASES = [
    dict(),
    dict(vcoarse=14.9),
    dict(vcoarse=-14.9),
    dict(vmod1=1.0),
    dict(vmod1=-1.0, vmod2=-0.5),
    dict(vmodr1=0.4, vmodr2=0.3),
    dict(ttune=0.0),
    dict(ttune=1.0),
    dict(lin=0.0),
    dict(lin=1.0),
    dict(vcoarse=-10.0, vfine=10.0, vmod1=2.0, ttune=0.8, lin=0.2),
]


@pytest.mark.parametrize(
    "case", BUS_CASES, ids=lambda c: "-".join(f"{k}{v}" for k, v in c.items()) or "default"
)
def test_bus_law_vs_theory(case):
    """V(bus) = V(rail) - 100k*Iref*exp(-Vb/VT) with the documented
    corrections. 5 mV tolerance: residual 2nd-order Early terms and the
    neglected Q402 base-current load (both sub-mV over this grid, margin
    for the exp's sensitivity to the 98R base-loading estimate)."""
    d = run_op(case)
    h = bus_hand(**case)
    assert d["rail"] == pytest.approx(h["rail"], abs=5e-3)
    assert d["bus"] == pytest.approx(h["bus"], abs=5e-3)


def test_exponential_volts_per_octave():
    """ln(Ic) is affine in the MOD1 pin voltage with slope -KDIV/VT:
    0.932 V/oct at the pin. 1.5%: Early + base-current corrections bend
    the pure e-fold law by ~1% at the 40 uA top of this sweep."""
    d = run_freqctl(
        {},
        """.control
dc V41 -1 1 0.1
wrdata freqctl_sw.txt v(bus) v(rail)
.endc""",
        "freqctl_sw.txt",
    )
    v, bus, rail = d[:, 0], d[:, 1], d[:, 3]
    ic = (rail - bus) / 100e3
    slope = np.polyfit(v, np.log(ic), 1)[0]  # positive: MOD1 up -> o41a down -> Ic up
    vpo_expected = np.log(2.0) / (KDIV / VT)  # 0.932 V/oct
    assert np.log(2.0) / slope == pytest.approx(vpo_expected, rel=0.015)
    assert np.all(np.diff(bus) < 0)  # monotonic: more MOD CV -> bus more negative


def test_total_tune_scales_iref():
    """Ic ratio between ttune=0 and ttune=1 equals the resistance ratio
    (620k+470k)/620k = 1.758 (1%: Early/base-current residuals)."""
    ics = []
    for t in (0.0, 1.0):
        d = run_op(dict(ttune=t))
        ics.append((d["rail"] - d["bus"]) / 100e3)
    assert ics[0] / ics[1] == pytest.approx(1090.0 / 620.0, rel=0.01)


def test_lineality_trim_shifts_bus():
    """VR402 spans the ~26.5 mV wiper divider; rail and bus follow 1:1
    (a fine offset trim of the whole law)."""
    d0, d1 = run_op(dict(lin=0.0)), run_op(dict(lin=1.0))
    dvw = vw_hand(1.0) - vw_hand(0.0)
    assert d1["rail"] - d0["rail"] == pytest.approx(dvw, abs=1e-3)
    assert d1["bus"] - d0["bus"] == pytest.approx(dvw, abs=1.5e-3)


def test_bus_output_impedance_regulated():
    """Within Q403's sinking range the loop absorbs load current: +-100 uA
    moves the bus < 2 mV (sub-ohm effective source impedance, loop-gain
    limited)."""
    d0 = run_op({})
    for il in (100e-6, -100e-6):
        d = run_op(dict(iload=il))
        assert abs(d["bus"] - d0["bus"]) < 2e-3, (
            f"iload={il}: bus moved {d['bus'] - d0['bus']:.4f} V"
        )


def test_bus_load_capacity_limit():
    """Q403 only sinks: once the drawn current exceeds |V(bus)|/10k +
    (100k/72k)*Ic (~0.17 mA at defaults) the transistor cuts off and the
    bus sags out of regulation toward the passive R424/R421 divider."""
    d0 = run_op({})
    cap = -d0["bus"] / 10e3 + (d0["rail"] - d0["bus"]) / 100e3 * (100.0 / 72.0)
    d = run_op(dict(iload=float(2.5 * cap)))
    assert d["bus"] - d0["bus"] < -0.05  # far outside the 2 mV regulated band


def test_mod1_ac_response():
    """Small-signal bus response to MOD1. Two verified features:
    - LF magnitude equals the DC difference quotient (linearized law;
      the IC42a servo is active below its ~430 Hz crossover)
    - at C401's 1.59 kHz corner the response is the composite of that
      pole (-3.0 dB) and the servo-dropout shelf: above the C402
      integrator's crossover the emitter rail is no longer regulated
      and partially follows the driven base through R414 || re(left),
      scaling the incremental expo gain by 1 - Z/(Z + re_R) ~= 0.73
      (-2.7 dB). 0.7 dB margin: the shelf's gradual transition region.
    """
    dd = 0.01
    b1 = run_op(dict(vmod1=+dd))["bus"]
    b0 = run_op(dict(vmod1=-dd))["bus"]
    slope_dc = abs(b1 - b0) / (2 * dd)
    d = run_freqctl(
        {},
        """.control
ac dec 20 1 100k
wrdata freqctl_ac.txt vdb(bus)
.endc""",
        "freqctl_ac.txt",
    )
    f, db = d[:, 0], d[:, 1]
    lf = db[np.argmin(np.abs(f - 10.0))]
    assert 10 ** (lf / 20.0) == pytest.approx(slope_dc, rel=0.05)
    # follower-division shelf: Z = R414 || re_L against re_R at the op point
    h = bus_hand()
    re_l = VT / iref_hand(0.5)
    re_r = VT / h["ic"]
    z = 1.0 / (1.0 / 2.2e3 + 1.0 / re_l)
    shelf_db = 20 * np.log10(1.0 - z / (z + re_r))
    fc = 1.0 / (2 * np.pi * 100e3 * 1e-9)
    at_fc = db[np.argmin(np.abs(f - fc))]
    assert at_fc - lf == pytest.approx(-3.01 + shelf_db, abs=0.7)
