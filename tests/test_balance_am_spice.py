"""Validate netlists/klm62d-balance-am.cir (KLM-62D balance mixer + AM)
against hand theory: mixer gain law, response flatness, balance-pot
crossfade law, the JFET ring-mod carrier null, and the AM depth law
(sideband levels from a two-tone transient).

The hand theory is a closed-form nodal reduction of the schematic's
resistive core (caps treated as shorts at 1 kHz, ideal opamps, JFET as
Rds = 1/(2*BETA*(Vgs-VTO)) at the trimmed operating point). It is derived
independently and mirrored by
dsp/balance_am.dsp; here it referees the netlist, in
tests/test_balance_am_dsp.py the netlist referees the DSP.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm62d-balance-am.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# netlist .param defaults (keep in sync with the deck's single .param line)
DEFAULTS = dict(
    bal=0.5,
    lvl=0.05,
    bias=0.40,
    rbal=0.5,
    cancel=0.5,
    intensity=1,
    acu=1,
    acl=0,
    acar=0.02,
    fcar=2000,
    alow=0,
    flow=1000,
    amod=0,
    fmod=200,
)

# --- hand theory ---------------------------------------------------------
# component values from the schematic
R201, R202, R207, R208 = 1e3, 22e3, 1.2e3, 22e3
R203, R209, R301, R302, R303 = 220.0, 220.0, 15e3, 220.0, 100e3
R304, R305, R306, R307 = 100e3, 22e3, 120e3, 220.0
# RBALPOT/RINTPOT values ASSUMED (see netlist); INTENSITY rheostat wiring and
# the VR301 divider wiring are CONFIRMED by the 2026-07-21 full-res re-read
RBALPOT, RINTPOT, RVR301, RLOAD = 100e3, 50e3, 47e3, 1e6
VTO, BETA = -1.5, 1.778e-3  # 2SK30-GR ASSUMED mid-spread (Idss 4 mA)
VBTOP = -14.9 * 10.0 / 43.0  # top of VR302 in the R310/VR302 divider
GMIN = 1e-9
# gate-node pole: R308||R309 = 2.35M driving C302 + JFET junction caps
FGATE = 1.0 / (2 * np.pi * (4.7e6 / 2) * 76e-12)  # ~891 Hz


def hand_gains(u=1.0, lo=0.0, bal=0.5, bias=0.40, rbal=0.5, intensity=1.0, vg=None):
    """Mid-band (caps short) nodal solve; returns (w, out26) for inputs u, lo.

    Derivation: mixer outputs vu=-22u, vl=-18.33l drive the balance pot;
    the wiper node W is loaded by the AM front-end (R301 + R302||Rds into the
    IC31a virtual ground), the ring-bal dry feed (R303+VR301 into the IC31b
    virtual ground) and the INTENSITY rheostat to the out26 node, which is
    itself driven by IC31b through R307 with out26 = q*W. Note q couples
    out26 back INTO W (positive feedback for under-pinched bias: the real
    circuit is unstable for e.g. bias<~0.14 at intensity=1, up to ~0.38 as
    intensity->0 - see model doc).
    """
    if vg is None:
        vg = VBTOP * bias
    g = max(2 * BETA * max(vg - VTO, 0.0), GMIN)
    rds = 1.0 / g
    p = R302 * rds / (R302 + rds)  # R302 || Rds
    zin1 = R301 + p  # W-node load of the JFET front-end
    # VR301 is a divider with the wiper at the IC31b virtual ground
    # (full-res re-read 2026-07-21): each side is a series feed.
    zr = R303 + RVR301 * rbal  # dry feed (R303 + VR301 top half)
    r305eff = R305 + RVR301 * (1 - rbal)  # wet feed (R305 + VR301 bottom half)
    gt = (R306 / r305eff) * (R304 * g) * (p / zin1) - R306 / zr  # out_amp = gt*W
    rint = max(RINTPOT * intensity, 1.0)
    ra = max(RBALPOT * (1 - bal), 1.0)
    rb = max(RBALPOT * bal, 1.0)
    gu, gl = 1.0 / (R203 + ra), 1.0 / (R209 + rb)
    q = (gt / R307 + 1.0 / rint) / (1.0 / R307 + 1.0 / rint + 1.0 / RLOAD)
    den = gu + gl + 1.0 / zin1 + 1.0 / zr + (1.0 - q) / rint
    w = (-(R202 / R201) * u * gu - (R208 / R207) * lo * gl) / den
    return w, q * w


BIAS_NULL = 0.4110  # hand-theory zero of gt at rbal=0.5 (gt_jfet = R306/zr)


# --- ngspice helpers -----------------------------------------------------
def run_deck(control: str, out_name: str, ngspice: str = "ngspice", **overrides):
    """Run the deck with substituted params and .control block; load wrdata."""
    p = dict(DEFAULTS, **overrides)
    deck = NETLIST.read_text()
    pline = ".param " + " ".join(f"{k}={v:g}" for k, v in p.items())
    deck = re.sub(r"^\.param .*$", pline, deck, count=1, flags=re.MULTILINE)
    deck = re.sub(r"\.control.*?\.endc", control, deck, flags=re.DOTALL)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            [ngspice, "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out_file = tmpdir / out_name
        if not out_file.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        return np.loadtxt(out_file)


def run_ac(**overrides) -> dict[str, np.ndarray]:
    """AC sweep; returns dB responses at the mixer outs, wiper and pin 26."""
    data = run_deck(
        ".control\nac dec 200 10 20k\n"
        "wrdata ac_out.txt vdb(uout) vdb(lout) vdb(w) vdb(out26)\n.endc",
        "ac_out.txt",
        **overrides,
    )
    return {
        "freq": data[:, 0],
        "uout": data[:, 1],
        "lout": data[:, 3],
        "w": data[:, 5],
        "out26": data[:, 7],
    }


def ac_gain_at(res: dict, node: str, f0: float) -> float:
    return float(res[node][np.argmin(np.abs(res["freq"] - f0))])


FS_TRAN = 48000
TSTOP = 0.14
T0, T1 = 0.04, 0.14  # analysis window: 40 ms settling, 0.1 s = integer cycles


def run_tran(**overrides) -> np.ndarray:
    """Transient of v(out26), resampled onto a uniform 1/FS_TRAN grid
    (ngspice wrdata writes the adaptive internal timesteps)."""
    data = run_deck(
        f".control\ntran 5u {TSTOP:g}\nwrdata tran_out.txt v(out26)\n.endc",
        "tran_out.txt",
        **overrides,
    )
    tg = np.arange(int(TSTOP * FS_TRAN)) / FS_TRAN
    return np.interp(tg, data[:, 0], data[:, 1])


def tone_amp(x: np.ndarray, f0: float, fs: float = FS_TRAN) -> float:
    """Amplitude of the f0 component over the T0..T1 window (rectangular
    projection; all test tones have an integer number of cycles in it)."""
    i0, i1 = int(round(T0 * fs)), int(round(T1 * fs))
    seg = x[i0:i1]
    t = np.arange(len(seg)) / fs
    return float(2.0 * abs(np.mean(seg * np.exp(-2j * np.pi * f0 * t))))


def dbv(a: float) -> float:
    return 20 * np.log10(max(a, 1e-15))


# --- tests: balance mixer ------------------------------------------------
def test_mixer_gains_match_theory():
    """IC21a/IC21b inverting gains: -R202/R201 = -22, -R208/R207 = -18.33."""
    res = run_ac()
    assert ac_gain_at(res, "uout", 1e3) == pytest.approx(20 * np.log10(22.0), abs=0.05)
    res = run_ac(acu=0, acl=1)
    assert ac_gain_at(res, "lout", 1e3) == pytest.approx(20 * np.log10(22.0 / 1.2), abs=0.05)


def test_mixer_flat():
    """No reactive elements inside the mixer loops: flat 30 Hz..10 kHz."""
    res = run_ac()
    m = (res["freq"] >= 30) & (res["freq"] <= 10e3)
    assert res["uout"][m].max() - res["uout"][m].min() < 0.05


def test_balance_pot_law():
    """Wiper level vs pot position matches the loaded-crossfade hand solve
    (the AM front-end + ring feed + intensity path load the wiper, so the
    law is NOT an ideal crossfade), and is monotonic toward the driven end."""
    gains = []
    for bal in [0.0, 0.25, 0.5, 0.75, 1.0]:
        res = run_ac(bal=bal)
        w_spice = ac_gain_at(res, "w", 1e3)
        w_hand, _ = hand_gains(u=1.0, lo=0.0, bal=bal)
        assert w_spice == pytest.approx(20 * np.log10(abs(w_hand)), abs=0.1), f"bal={bal}"
        gains.append(w_spice)
    assert np.all(np.diff(gains) > 0)


# --- tests: amplitude modulator ------------------------------------------
def test_out26_matches_hand_theory():
    """Static chain gain to pin 26 across bias/intensity/rbal settings.
    All settings sit in the stable region of the intensity-pot feedback
    loop."""
    for over in [
        dict(),
        dict(bias=0.35),
        dict(bias=0.50),
        dict(bias=0.43, intensity=0.2),
        dict(bias=0.43, intensity=0.02),
        dict(rbal=1.0, bias=0.43),
        dict(rbal=0.0, bias=0.43),
    ]:
        p = dict(DEFAULTS, **over)
        res = run_ac(**over)
        _, o_hand = hand_gains(
            u=1.0, lo=0.0, bal=p["bal"], bias=p["bias"], rbal=p["rbal"], intensity=p["intensity"]
        )
        assert ac_gain_at(res, "out26", 1e3) == pytest.approx(
            20 * np.log10(abs(o_hand)), abs=0.1
        ), f"{over}"


def test_carrier_null_bias():
    """VR302 has a deep carrier null (AM -> ring modulation) where the JFET
    path gain equals the R303+VR301 dry path: hand theory puts it at
    bias = 0.4110 for rbal = 0.5. The null is razor sharp, so assert the
    minimum over a fine scan sits at the predicted bias and is deep."""
    grid = np.arange(0.399, 0.425, 0.002)
    gains = [ac_gain_at(run_ac(bias=b), "out26", 1e3) for b in grid]
    ref = ac_gain_at(run_ac(bias=0.40), "out26", 1e3)
    i = int(np.argmin(gains))
    assert abs(grid[i] - BIAS_NULL) <= 0.002
    assert gains[i] < ref - 25.0


def test_am_depth_law():
    """Two-tone transient (2 kHz carrier via the upper mixer, 200 Hz mod):
    sideband/carrier ratio matches the small-signal hand prediction
    0.5 * (dG/dVg) * mg / G, and doubling the mod level moves the first
    sidebands by +6 dB (multiplicative AM law)."""
    y1 = run_tran(amod=0.5)
    y2 = run_tran(amod=1.0)
    car = tone_amp(y1, 2000.0)
    lsb, usb = tone_amp(y1, 1800.0), tone_amp(y1, 2200.0)

    # small-signal prediction, gate amplitude = 0.5*lvl*amod through the
    # 891 Hz gate-node pole
    vg0 = VBTOP * DEFAULTS["bias"]
    d = 1e-5
    gp = abs(hand_gains(vg=vg0 + d)[1])
    gm = abs(hand_gains(vg=vg0 - d)[1])
    g0 = abs(hand_gains(vg=vg0)[1])
    mg = 0.5 * DEFAULTS["lvl"] * 0.5 / np.sqrt(1.0 + (200.0 / FGATE) ** 2)
    expect_ratio = 0.5 * ((gp - gm) / (2 * d)) * mg / g0
    assert usb / car == pytest.approx(expect_ratio, rel=0.05)
    assert lsb / usb == pytest.approx(1.0, abs=0.02)  # symmetric AM sidebands

    # depth doubles with mod level; carrier stays (to first order)
    assert dbv(tone_amp(y2, 2200.0)) - dbv(usb) == pytest.approx(6.02, abs=0.3)
    assert dbv(tone_amp(y2, 2000.0)) - dbv(car) == pytest.approx(0.0, abs=0.2)


def test_mod_alone_produces_no_output():
    """The JFET modulates gain; with no carrier there is nothing to modulate.
    Residual mod feedthrough (C302/Cgd paths) stays ~60 dB below the levels
    the AM case puts out."""
    y = run_tran(acar=0.0, amod=2.5)
    assert dbv(tone_amp(y, 200.0)) < -70.0
    assert dbv(tone_amp(y, 2000.0)) < -90.0


def test_intensity_pot_kills_modulation():
    """AM INTENSITY at minimum (rheostat -> ~0 ohm) ties the wiper node to
    the out26 node, which R307 clamps to the IC31b output. Near the ring
    trim (gt <= 0) that collapses the modulated content - the sidebands
    drop by tens of dB. The rheostat wiring (pin 24 dry node -> pin 26) is
    CONFIRMED by the 2026-07-21 full-res re-read, so this collapse is real
    hardware behavior; only the pot VALUE (50k) remains assumed. The old
    scan-doc gloss ("low resistance = dry dominates") only holds for
    under-pinched bias where the JFET path gain is ~+1."""
    hi = run_tran(amod=2.5, bias=0.43)
    lo = run_tran(amod=2.5, bias=0.43, intensity=0.0)
    assert dbv(tone_amp(lo, 2200.0)) < dbv(tone_amp(hi, 2200.0)) - 15.0
