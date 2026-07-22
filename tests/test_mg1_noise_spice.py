"""Validate the KLM-63 MG1 + noise netlist against hand theory (plan-style
SPICE-vs-theory gate for this board):

  - LFO frequency follows the exponential rate law of the IC31a/Q301/Q302
    servo converter: f = I_ref * exp(Vcv/VT) / (4 * C302 * Vth), with the
    FREQ ADJ pot's wiper impedance included in the CV divider
  - waveform levels come from the build-out dividers (pins 34/35/36/37)
  - the sine shaper (wiring confirmed by the full-res scan re-read)
    produces its rectified wave at 2x the LFO rate
  - white-noise chain: flat in-band, absolute gain per the corrected
    topology (IC35a non-inverting, gain leg C310 -> R333 -> VR304 rheostat;
    then IC35b -R335/R334); pink stage matches the exact Zfb/Zin transfer
    of the ladder (corner pattern 1.06 Hz / 106 Hz / 10.6 kHz, C315 assumed
    1.5 nF - no value is printed on the sheet)

The transcription's behavioral choices these tests lean on (Schmitt
thresholds from the RHYS 35.75k hysteresis resistor - panel-anchored to the
5VP-P MG 1 OUT print, so the waveform level tests anchor at 5 Vpp on pin
34 - ideal current steering, behavioral avalanche source) are documented in
the netlist header.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm63-mg1-noise.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# --- hand theory (mirrors the netlist header and dsp/mg1_noise.dsp) ---
VT = 0.025865  # kT/q at ngspice's 27 C default
IREF = 14.9 / 10e6
C302 = 0.1e-6
VTH = 13.0 * 10.0 / 35.75  # Schmitt threshold, RHYS 35.75k panel-anchored
# (5VP-P MG 1 OUT print -> pin 34 swings +/-2.5 V exactly: OUTDIV*VTH = 2.5)
OUTDIV = 2.2 / 3.2  # 1k/2.2k pin build-outs
SQDIV = 680.0 / 3680.0  # R328/R329


def radj(fadj: float) -> float:
    """R303 in series with the FREQ ADJ wiper impedance (100k pot)."""
    return 470e3 + 100e3 * fadj * (1.0 - fadj)


def vcv(vfc1: float = 0.0, vfc2: float = 0.0, fadj: float = 0.5) -> float:
    gtot = 1 / 1.8e3 + 1 / 56e3 + 1 / 100e3 + 1 / radj(fadj) + 1 / 330e3
    return (vfc1 / 56e3 + vfc2 / 100e3 + 14.9 / 330e3 - 14.9 * fadj / radj(fadj)) / gtot


def f_hand(vfc1: float = 0.0, vfc2: float = 0.0, fadj: float = 0.5) -> float:
    """Rate law with the one-step base-current correction: Q301's Ib = Ic/beta
    loads the CV divider (Thevenin ~1.7k), sagging the law by ~2.4% at the
    ~110 uA top of the tested range (beta = 300 per the netlist QC945 model;
    one fixed-point step leaves <0.1% residual)."""
    gtot = 1 / 1.8e3 + 1 / 56e3 + 1 / 100e3 + 1 / radj(fadj) + 1 / 330e3
    ic0 = IREF * np.exp(vcv(vfc1, vfc2, fadj) / VT)
    ic = ic0 * np.exp(-ic0 / 300.0 / gtot / VT)
    return ic / (4 * C302 * VTH)


def white_gain_hand(f: np.ndarray, ng: float = 0.5) -> np.ndarray:
    """|gain| junction -> pin 41 for the full-res-confirmed topology:
    IC35a non-inverting, A1 = 1 + R332/(R333 + VR304 + 1/sC310), then C311
    into IC35b's -R335/R334; both blocking caps exact."""
    w = 2j * np.pi * f
    rng = max(47e3 * (1 - ng), 1.0)
    a1 = 1.0 + 1e6 / (4.7e3 + rng + 1 / (w * 10e-6))
    a2 = 1e6 / (82e3 + 1 / (w * 10e-6))
    return np.abs(a1 * a2)


def pink_gain_hand(f: np.ndarray) -> np.ndarray:
    """|H| of the IC31b stage: Zfb (ladder, with the deck's 1G DC aid in
    parallel) over Zin = R337 + C312."""
    w = 2j * np.pi * f
    y = (
        w * 1.5e-9
        + w * 1.5e-9 / (1 + w * 10e3 * 1.5e-9)
        + w * 15e-9 / (1 + w * 100e3 * 15e-9)
        + w * 150e-9 / (1 + w * 1e6 * 150e-9)
        + 1e-9
    )
    return np.abs(1 / y / (10e3 + 1 / (w * 10e-6)))


# --- deck runner ---
def run_mg1(params: dict, control: str, outfile: str) -> np.ndarray:
    """Substitute the .param line and .control block, run ngspice, return the
    wrdata array (columns [t, v1, t, v2, ...])."""
    base = dict(vfc1=0, vfc2=0, fadj=0.5, voff=0, ng=0.5, oscon=1, itrim=0)
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


_tran_cache: dict[float, np.ndarray] = {}


def lfo_tran(vfc1: float) -> dict[str, np.ndarray]:
    """Transient of the LFO at a rate setting; ~9 cycles after settling."""
    if vfc1 not in _tran_cache:
        fh = f_hand(vfc1=vfc1)
        tstart, tstop, tstep = 0.5 / fh, 9.5 / fh, 1.0 / (400 * fh)
        control = f""".control
tran {tstep:g} {tstop:g} {tstart:g} uic
wrdata mg1_tran.txt v(p34) v(p35) v(p36) v(p37) v(tri)
.endc"""
        _tran_cache[vfc1] = run_mg1({"vfc1": vfc1}, control, "mg1_tran.txt")
    d = _tran_cache[vfc1]
    return {
        "t": d[:, 0],
        "p34": d[:, 1],
        "p35": d[:, 3],
        "p36": d[:, 5],
        "p37": d[:, 7],
        "tri": d[:, 9],
    }


def measured_freq(t: np.ndarray, x: np.ndarray) -> float:
    """1/mean(period) via interpolated rising crossings of the mean."""
    x = x - np.mean(x)
    idx = np.where((x[:-1] < 0) & (x[1:] >= 0))[0]
    tc = t[idx] + (t[idx + 1] - t[idx]) * (-x[idx]) / (x[idx + 1] - x[idx])
    assert len(tc) >= 3, "too few cycles captured"
    return 1.0 / np.mean(np.diff(tc))


RATE_SET = [0.0, 1.0, 2.0]  # volts at pin 43 (FREQ CONT I)
# non-idealities vs the hand law, all sub-percent (Q301 base current loading
# the CV divider ~0.1%, comparator transition softness, Early-term residue):
FREQ_RTOL = 0.02


@pytest.mark.parametrize("vfc1", RATE_SET)
def test_lfo_frequency_matches_theory(vfc1):
    d = lfo_tran(vfc1)
    f = measured_freq(d["t"], d["tri"])
    assert f == pytest.approx(f_hand(vfc1=vfc1), rel=FREQ_RTOL), (
        f"vfc1={vfc1}: SPICE {f:.3f} Hz vs theory {f_hand(vfc1=vfc1):.3f} Hz"
    )


def test_lfo_rate_law_exponential():
    """The rate is exponential in the pin-43 CV: each measured ratio must
    match exp(dVcv/VT) - the servo's e-fold law through the 56k/1.8k divider
    (~1.69 oct/V)."""
    freqs = [measured_freq(lfo_tran(v)["t"], lfo_tran(v)["tri"]) for v in RATE_SET]
    for v0, v1, f0, f1 in zip(RATE_SET, RATE_SET[1:], freqs, freqs[1:]):
        expect = np.exp((vcv(vfc1=v1) - vcv(vfc1=v0)) / VT)
        # 3%: the pure e-fold law minus the base-current sag (f_hand), which
        # costs ~1.7% on the 1->2 V ratio at these collector currents
        assert f1 / f0 == pytest.approx(expect, rel=0.03)
        assert f1 / f0 == pytest.approx(f_hand(vfc1=v1) / f_hand(vfc1=v0), rel=0.02)


def test_lfo_waveform_levels():
    """Triangle/square levels and symmetry against the divider theory;
    the triangle anchors at the panel's 5 Vpp (pin 34 +/-2.5 V = OUTDIV*VTH)."""
    d = lfo_tran(0.0)
    assert d["tri"].max() == pytest.approx(VTH, rel=0.02)
    assert d["tri"].min() == pytest.approx(-VTH, rel=0.02)
    assert d["p34"].max() == pytest.approx(OUTDIV * VTH, rel=0.02)
    assert np.allclose(d["p35"], -d["p34"], atol=1e-3)  # IC33 unity inverter
    assert d["p36"].max() == pytest.approx(SQDIV * 13.0, rel=0.02)
    assert d["p36"].min() == pytest.approx(-SQDIV * 13.0, rel=0.02)
    duty = np.mean(d["p36"] > 0)
    assert 0.45 < duty < 0.55  # ideal steering is symmetric; itrim=0


def test_sine_shaper_as_read():
    """The D307/D308 wiring (confirmed by the full-res scan re-read: both
    diodes anode-at-buffer into the one summing node) rectifies the
    complementary triangles: pin 37 is a diode-rounded rectified triangle at
    2x the LFO rate with max = outdiv * R322 * (14.9/R325) (both diodes off)
    and min set by the diode knee (vd ~= 0.574 V effective at the ~31 uA
    peak diode current). This is the genuine circuit behavior, not a
    misread."""
    d = lfo_tran(0.0)
    f_tri = measured_freq(d["t"], d["tri"])
    f_sin = measured_freq(d["t"], d["p37"])
    assert f_sin == pytest.approx(2 * f_tri, rel=0.02)
    top = OUTDIV * (14.9 / 750e3 * 220e3)
    bot = OUTDIV * (14.9 / 750e3 * 220e3 - 2.2 * (VTH - 0.574))
    assert d["p37"].max() == pytest.approx(top, rel=0.03)
    # 5%: the piecewise-linear knee model vs the true exponential diode
    assert d["p37"].min() == pytest.approx(bot, rel=0.05)


AC_CONTROL = """.control
ac dec 40 0.1 100k
wrdata mg1_ac.txt vdb(p41) vdb(p42)
.endc"""


def noise_ac(ng: float) -> dict[str, np.ndarray]:
    d = run_mg1({"ng": ng, "oscon": 0}, AC_CONTROL, "mg1_ac.txt")
    return {"f": d[:, 0], "p41": d[:, 1], "p42": d[:, 3]}


@pytest.mark.parametrize("ng", [0.25, 0.5, 1.0])
def test_white_chain_gain_and_flatness(ng):
    d = noise_ac(ng)
    band = (d["f"] >= 20) & (d["f"] <= 20e3)
    hand = 20 * np.log10(white_gain_hand(d["f"][band], ng))
    err = np.abs(d["p41"][band] - hand)
    assert err.max() < 0.2, f"ng={ng}: white gain off by {err.max():.2f} dB"
    # flat in-band: C310's gain-leg shelf costs at most ~0.13 dB at 20 Hz
    # when VR304 is at 0 ohm (ng=1); C311's 0.19 Hz corner is far below
    assert d["p41"][band].max() - d["p41"][band].min() < 0.2


def test_pink_stage_matches_ladder_theory():
    """SPICE pink/white ratio vs the exact Zfb/Zin transfer, 1 Hz..20 kHz."""
    d = noise_ac(0.5)
    band = (d["f"] >= 1) & (d["f"] <= 20e3)
    hand = 20 * np.log10(pink_gain_hand(d["f"][band]))
    err = np.abs((d["p42"] - d["p41"])[band] - hand)
    assert err.max() < 0.2, f"pink stage off by {err.max():.2f} dB"


def test_pink_slope_and_corners():
    """The ladder gives the scan's ~ -3 dB/oct pinking: with C315 assumed
    1.5 nF the exact average slope 100 Hz..10 kHz is -12.4 dB/decade
   ."""
    d = noise_ac(0.5)
    pink = d["p42"] - d["p41"]

    def at(freq):
        return pink[np.argmin(np.abs(d["f"] - freq))]

    slope = (at(10e3) - at(100.0)) / 2.0
    assert -13.5 < slope < -10.0
    # LF plateau: the C312 coupling cancels the ladder's DC pole at
    # -C312/(C314+..+C317) = 35.5 dB
    assert at(0.15) == pytest.approx(20 * np.log10(10e-6 / 168e-9), abs=0.3)
