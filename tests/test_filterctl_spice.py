"""Validate netlists/klm63-filterctl.cir (KLM-63 FILTER CONTROL - the FCU/FCL
cutoff-CV bus producer) against hand theory.

The board is purely resistive (no capacitors anywhere in the CV path), so the
hand theory is an exact nodal solve: unity inverting summer with a loaded-
wiper trim offset, the asymmetric BAL feed (+0.8 into the FCU amp's + input,
-2/3 into the FCL amp's - input; full-res re-read 2026-07-21), +/-13 V op-amp
clamps, and the R20/R21/R22 (R26/R27/R28) output pads that turn the op-amp
swing into a stiff ~14.5 ohm, ~-0.47..+0.04 V bus.  The same algebra is
mirrored by dsp/filterctl.dsp; here it referees the netlist, in
tests/test_filterctl_dsp.py the netlist referees the DSP.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm63-filterctl.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# netlist .param defaults (keep in sync with the deck's single .param line)
DEFAULTS = dict(
    vfc=0.0, vmod1=0.0, vmod2=0.0, vbal=0.0, fcadj=0.5, rloadu=1e12, rloadl=1e12, rloadb=1e12
)

# --- hand theory ---------------------------------------------------------
R_IN = 100e3  # R9/R10/R11 = R13 = R15 = R16/R17 = R23/R25 (all 100k)
R12, RVR1, R14 = 100e3, 100e3, 22e3
R18, R19, R24 = 150e3, 100e3, 150e3
R20, R21, R22 = 750.0, 1e3, 15.0
R26, R27, R28 = 750.0, 1.5e3, 15.0
R29 = 10e3
VNEG = -14.9
VSAT = 13.0  # 4558 output clamp used by the deck (see netlist header)

# output-pad constants: v_bus = K*vo + O, Zout = 1/G
GU = 1 / R20 + 1 / R21 + 1 / R22
KU, OU, ZU = (1 / R20) / GU, (VNEG / R21) / GU, 1 / GU  # 0.019324, -0.215942, 14.49
GL = 1 / R26 + 1 / R27 + 1 / R28
KL, OL, ZL = (1 / R26) / GL, (VNEG / R27) / GL, 1 / GL  # 0.019417, -0.144660, 14.56


def offset(fcadj: float) -> float:
    """IC1a output offset from the FC ADJ leg: VR1 wiper Thevenin loaded by
    R13 into the virtual ground, times -R15/R13 = -1."""
    pa = R12 + RVR1 * fcadj  # ground side of the wiper
    pb = RVR1 * (1 - fcadj) + R14  # -14.9 V side
    vth = VNEG * pa / (pa + pb)
    rth = pa * pb / (pa + pb)
    return -vth * R_IN / (rth + R_IN)


def clamp(v: float) -> float:
    return float(np.clip(v, -VSAT, VSAT))


def hand(vfc=0.0, vmod1=0.0, vmod2=0.0, vbal=0.0, fcadj=0.5, rloadu=1e12, rloadl=1e12):
    """Exact node solve; returns dict of vo1..vo3 and the loaded bus volts."""
    vo1 = clamp(offset(fcadj) - (vfc + vmod1 + vmod2))
    vo2 = clamp(-vo1 + 2 * (R19 / (R18 + R19)) * vbal)  # +0.8*BAL, non-inv lift
    vo3 = clamp(-vo1 - (R_IN / R24) * vbal)  # -2/3*BAL, inverting sum
    fcu = (KU * vo2 + OU) * rloadu / (rloadu + ZU)
    fcl = (KL * vo3 + OL) * rloadl / (rloadl + ZL)
    return dict(vo1=vo1, vo2=vo2, vo3=vo3, fcu=fcu, fcl=fcl)


# --- ngspice helpers -----------------------------------------------------
def run_op(**overrides) -> dict[str, float]:
    """Run the deck's .op with substituted params; parse the printed nodes."""
    p = dict(DEFAULTS, **overrides)
    deck = NETLIST.read_text()
    pline = ".param " + " ".join(f"{k}={v:g}" for k, v in p.items())
    deck = re.sub(r"^\.param .*$", pline, deck, count=1, flags=re.MULTILINE)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            ["ngspice", "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
    out = {}
    for m in re.finditer(r"v\((\w+)\)\s*=\s*([-+0-9.eE]+)", proc.stdout):
        out[m.group(1)] = float(m.group(2))
    if not out:
        raise RuntimeError(f"ngspice produced no .op output:\n{proc.stdout}\n{proc.stderr}")
    return out


# --- tests ---------------------------------------------------------------
def test_hand_theory_grid():
    """Exact algebra vs SPICE over a grid spanning linear and clipped
    regions of all three op-amps.  Tolerances: the deck's ideal op-amps have
    finite gain 1e6, so the vo nodes carry a loop-error ~|vo|/1e6 relative to
    the infinite-gain algebra (measured <= 2.1e-4 V at vo1 = +11.2 V); the
    bus pins divide that by ~52, so they agree to microvolts."""
    cases = [
        dict(),
        dict(vfc=-3.0),
        dict(vfc=2.0, vmod1=-1.0, vmod2=0.5),
        dict(vmod1=3.3, vmod2=-3.3),
        dict(vbal=5.0),
        dict(vbal=-5.0, vfc=-2.0),
        dict(fcadj=0.0),
        dict(fcadj=1.0),
        dict(fcadj=1.0, vfc=-10.0),  # IC1a clips at +13
        dict(vfc=15.0, vmod1=8.0),  # IC1a linear negative, vo2 clips at +13
        dict(vfc=-15.0, vmod2=-10.0, vbal=10.0),  # both output amps clipped
    ]
    for over in cases:
        sp = run_op(**over)
        th = hand(**{k: v for k, v in over.items()})
        for node in ["vo1", "vo2", "vo3"]:
            assert sp[node] == pytest.approx(th[node], abs=5e-4), f"{node} @ {over}"
        for node in ["fcu", "fcl"]:
            assert sp[node] == pytest.approx(th[node], abs=1e-5), f"{node} @ {over}"


def test_input_summing_weights():
    """FC, MOD1, MOD2 all enter with identical weight (100k into a 100k
    unity summer): each contributes KU = +19.32 mV/V on FCU and
    KL = +19.42 mV/V on FCL (double inversion -> non-inverting at the pin)."""
    base = run_op()
    for pin in ["vfc", "vmod1", "vmod2"]:
        up = run_op(**{pin: 1.0})
        assert up["fcu"] - base["fcu"] == pytest.approx(KU, abs=1e-5), pin
        assert up["fcl"] - base["fcl"] == pytest.approx(KL, abs=1e-5), pin


def test_bal_moves_buses_in_opposite_directions():
    """BAL is a complementary crossfade: +0.8 gain into the FCU amp (non-inv
    lift through R18/R19) and -2/3 into the FCL amp (R24 inverting sum).
    Sensitivities at the pins: +15.46 mV/V (FCU), -12.94 mV/V (FCL)."""
    base = run_op()
    up = run_op(vbal=1.0)
    dfcu = up["fcu"] - base["fcu"]
    dfcl = up["fcl"] - base["fcl"]
    assert dfcu == pytest.approx(0.8 * KU, abs=1e-5)
    assert dfcl == pytest.approx(-(2.0 / 3.0) * KL, abs=1e-5)
    assert dfcu > 0 > dfcl


def test_fcadj_trim_law():
    """FC ADJ (VR1) sets the quiescent point through the loaded wiper:
    IC1a idles at +4.33/+6.77/+11.20 V for fcadj = 0/0.5/1, i.e. FCU trims
    -0.300/-0.347/-0.432 V (monotonic, exact hand law)."""
    prev = None
    for fcadj, vo1_expect in [(0.0, 4.3314), (0.5, 6.7727), (1.0, 11.2030)]:
        sp = run_op(fcadj=fcadj)
        assert sp["vo1"] == pytest.approx(vo1_expect, abs=5e-4)
        assert sp["fcu"] == pytest.approx(KU * (-sp["vo1"]) + OU, abs=1e-5)
        if prev is not None:
            assert sp["fcu"] < prev  # more offset -> more negative bus
        prev = sp["fcu"]


def test_output_impedance():
    """The 15R legs make the buses stiff: Zout = R20||R21||R22 = 14.49 ohm
    (FCU) and R26||R27||R28 = 14.56 ohm (FCL), measured by loading with 1k."""
    voc = run_op()
    vl = run_op(rloadu=1e3, rloadl=1e3)
    zu = 1e3 * (voc["fcu"] / vl["fcu"] - 1.0)
    zl = 1e3 * (voc["fcl"] / vl["fcl"] - 1.0)
    assert zu == pytest.approx(ZU, rel=0.01)
    assert zl == pytest.approx(ZL, rel=0.01)


def test_bus_immune_to_note_loading():
    """12 KORG35 channels loading the bus through their per-note 47k series
    resistors (KLM-69 side) sag it by only Zout/(Zout + 47k/12) < 0.4 % -
    this is why the pads are built around a 15 ohm leg."""
    voc = run_op()
    vl = run_op(rloadu=47e3 / 12, rloadl=47e3 / 12)
    sag_u = 1.0 - vl["fcu"] / voc["fcu"]
    sag_l = 1.0 - vl["fcl"] / voc["fcl"]
    assert sag_u == pytest.approx(ZU / (ZU + 47e3 / 12), rel=0.01)
    assert 0 < sag_u < 0.004
    assert 0 < sag_l < 0.004


def test_bus_clip_range():
    """With the op-amps clamped at +/-13 V the buses span
    FCU: -0.467..+0.035 V, FCL: -0.397..+0.108 V - the absolute range the
    KORG35 cells can ever see (mV-scale by design; the CV attenuation of the
    MS-10-style 47k/680 divider lives on this board)."""
    lo = run_op(vfc=25.0)  # vo1 -> -13 territory? no: vfc positive -> vo1 negative -> vo2 +13
    hi = run_op(vfc=-25.0)
    assert lo["fcu"] == pytest.approx(KU * VSAT + OU, abs=1e-4)  # +0.0353
    assert hi["fcu"] == pytest.approx(-KU * VSAT + OU, abs=1e-4)  # -0.4671
    assert lo["fcl"] == pytest.approx(KL * VSAT + OL, abs=1e-4)  # +0.1078
    assert hi["fcl"] == pytest.approx(-KL * VSAT + OL, abs=1e-4)  # -0.3971


def test_fcl_sits_above_fcu():
    """R27 1.5k vs R21 1k (drawn asymmetry, verified at full res): at equal
    op-amp drive the FCL pin sits ~71 mV above FCU (offset -0.145 vs -0.216,
    gains within 0.5 %)."""
    sp = run_op()
    assert sp["vo2"] == pytest.approx(sp["vo3"], abs=1e-6)  # vbal = 0
    assert sp["fcl"] - sp["fcu"] == pytest.approx((KL - KU) * sp["vo2"] + (OL - OU), abs=1e-5)
    assert 0.055 < sp["fcl"] - sp["fcu"] < 0.075


def test_fc_bias():
    """Pin 4 is -14.9 V behind R29 10k: open-circuit -14.9 V, halved into a
    matched 10k load (the panel FILTER CUTOFF slider supply, off-board)."""
    assert run_op()["fcbias"] == pytest.approx(-14.9, abs=1e-6)
    assert run_op(rloadb=10e3)["fcbias"] == pytest.approx(-7.45, abs=1e-4)
