"""Validate netlists/klm64-siggen.cir (KLM-64E signal generator, one note
channel) against hand theory: master-VCO frequency law over the page-0010
tuning-cap chart and the KLM-62D temperament bus, divider ratios, and the
waveshaper staircase/mode behavior at the real KLM-63 WFD/WFR rail levels
.

Also exports the shared ngspice transient helpers (run_siggen, master_freq,
staircase_harmonics, CHART...) reused by tests/test_siggen_dsp.py for the
DSP-vs-SPICE comparison.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm64-siggen.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# page-0010 tuning-cap chart, pF (card 1 F..A#, card 2 B..E); the netlist
# models CT1 and CT2 in parallel (their sum tracks 2^(-n/12) within ~1%,
# which is the evidence for the parallel reading)
CHART = {
    "F": (1500, 47),
    "F#": (1200, 270),
    "G": (1200, 180),
    "G#": (1200, 100),
    "A": (1000, 220),
    "A#": (1000, 150),
    "B": (1000, 100),
    "C": (1000, 33),
    "C#": (820, 150),
    "D": (820, 100),
    "D#": (820, 47),
    "E": (820, 0),
}
NOTES = list(CHART)

# KLM-62D temperament-bus facts: the pitch
# rail the card pin actually carries. One bus octave = a doubling of
# (VRAIL - vbus).
VBUS_NEUTRAL = -1.62
VRAIL_BUS = -0.55


def bus_octave(n: float) -> float:
    """Bus voltage n octaves above the neutral -1.62 V point."""
    return VRAIL_BUS - (VRAIL_BUS - VBUS_NEUTRAL) * 2.0**n


# netlist calibration (trim/span rewritten into the .param line): span=1.0
# nulls the Vbe/rail tracking offset (exact f-doubling neutral -> -2.69 V),
# trim=0.99 puts note A at 1760 Hz at the bus neutral. Both near end of
# travel.
TRIM = 0.99
SPAN = 1.0
RCHG = 62e3 + 10e3 * TRIM  # R21 + VR11 rheostat at the calibration
DV_RAMP = 6.65  # ramp swing: release at vrail-0.20 down to the 0.30 V trip
TDIS_K = 1050 * np.log(6.85 / 0.20)  # discharge tail per farad (R141+RON)

# KLM-63 rail levels per panel selection: (wfd, wfr)
RAILS = {
    "saw": (0.0, 14.83),
    "tri-lo": (0.4, 8.67),
    "tri-mid": (0.4, 11.32),
    "tri-hi": (0.4, 13.5),
    "p-wide": (7.37, 0.36),
    "p-mid": (8.99, 0.36),
    "p-nar": (11.52, 0.36),
    "pwm": (9.95, 0.0),
}

# wrdata column layout of siggen_out.txt: (t, v) pairs in this order
COLS = ["vc", "ne11", "vq1", "vq2", "vq3", "ns", "nsrc", "nout"]


def run_siggen(
    note: str = "A",
    vbus: float = VBUS_NEUTRAL,
    wfd: float = 0.0,
    wfr: float = 14.83,
    vsq: float = 5.2,
    vmid: float = 7.45,
    trim: float = TRIM,
    span: float = SPAN,
) -> dict[str, np.ndarray]:
    """One 20 ms transient (2 ms settle) of the signal-generator netlist."""
    c1, c2 = CHART[note]
    return _run_cached(
        c1,
        c2,
        float(vbus),
        float(wfd),
        float(wfr),
        float(vsq),
        float(vmid),
        float(trim),
        float(span),
    )


@lru_cache(maxsize=96)
def _run_cached(c1, c2, vbus, wfd, wfr, vsq, vmid, trim, span) -> dict[str, np.ndarray]:
    deck = NETLIST.read_text()
    pline = (
        f".param CT1={c1}p CT2={max(c2, 1e-6):g}p vbus={vbus} trim={trim} "
        f"span={span} vsq={vsq} vmid={vmid} wfd={wfd} wfr={wfr} rload=100k"
    )
    deck = re.sub(r"^\.param .*$", pline, deck, count=1, flags=re.MULTILINE)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            ["ngspice", "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out = tmpdir / "siggen_out.txt"
        if not out.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        data = np.loadtxt(out)
    res = {"t": data[:, 0]}
    for i, name in enumerate(COLS):
        res[name] = data[:, 2 * i + 1]
    return res


def crossing_freq(t: np.ndarray, v: np.ndarray) -> float:
    """Mean frequency from interpolated mid-level rising crossings."""
    mid = 0.5 * (v.min() + v.max())
    c = np.where((v[:-1] < mid) & (v[1:] >= mid))[0]
    tc = t[c] + (mid - v[c]) * (t[c + 1] - t[c]) / (v[c + 1] - v[c])
    return 1.0 / np.diff(tc).mean()


def master_freq(res: dict[str, np.ndarray]) -> float:
    return crossing_freq(res["t"], res["vc"])


def staircase_harmonics(t: np.ndarray, x: np.ndarray, tst: float, kmax: int = 7) -> np.ndarray:
    """Complex Fourier coefficients c_0..c_kmax of x over an integer number
    of staircase periods tst, via trapezoid integration on the (possibly
    non-uniform) time grid."""
    n = int((t[-1] - t[0]) / tst)
    m = t <= t[0] + n * tst
    tt, xx = t[m], x[m]
    return np.array(
        [
            np.trapezoid(xx * np.exp(-2j * np.pi * k * tt / tst), tt) / (n * tst)
            for k in range(kmax + 1)
        ]
    )


def ladder_levels(vsq: float = 5.2, vmid: float = 7.45) -> np.ndarray:
    """Expected 8-slot staircase at the summing node, counting order (row-3
    pools 100k/200k/200k: the two upper taps carry EQUAL weight, so the 8
    slots visit 5 distinct levels with 1/8-2/8-2/8-2/8-1/8 occupancy)."""
    n = np.arange(8)
    b0, b1, b2 = n & 1, (n >> 1) & 1, (n >> 2) & 1
    return vmid + vsq * (2 * (b2 - 0.5) + (b1 - 0.5) + (b0 - 0.5)) / 4.0


def duty_high(res: dict[str, np.ndarray], mid: float | None = None) -> float:
    """High fraction of the shaper output over an integer number of
    staircase cycles (a fractional trailing cycle biases the estimate by
    up to ~3% on the 4.4 cycles a transient holds)."""
    t, nout = res["t"], res["nout"]
    tst = 8.0 / master_freq(res)
    m = t <= t[0] + int((t[-1] - t[0]) / tst) * tst
    if mid is None:
        mid = 0.5 * (nout.min() + nout.max())
    return float(np.mean(nout[m] > mid))


# ---------------------------------------------------------------------------


def test_master_frequency_hand_theory():
    """Q11 is a current source into the timing caps: T = DV*Ct/I + tdis with
    I read from the simulated emitter node ((Ve - vbus)/R, alpha-corrected).
    2.5% tolerance: Early-effect modulation of I along the ramp (~VAF=100)
    and the reset trip resolution are ~1% effects, not loosened physics."""
    for note in ["F", "A", "E"]:
        res = run_siggen(note=note)
        ct = sum(CHART[note]) * 1e-12
        i_chg = (res["ne11"].mean() - VBUS_NEUTRAL) / RCHG * (300.0 / 301.0)
        t_theory = DV_RAMP * ct / i_chg + TDIS_K * ct
        f = master_freq(res)
        assert f == pytest.approx(1.0 / t_theory, rel=0.025), (
            f"{note}: SPICE {f:.1f} Hz vs theory {1 / t_theory:.1f} Hz"
        )


def test_note_chart_tracks_tuning_caps():
    """f*(CT1+CT2) constant across notes (the chart's 1/C law; the ~1% spread
    is the C-dependent discharge fraction) and A calibrated to 1760 Hz
   ."""
    prods = {}
    for note in ["F", "G#", "A", "C", "D#", "E"]:
        res = run_siggen(note=note)
        f = master_freq(res)
        prods[note] = f * sum(CHART[note]) * 1e-12
    ref = prods["A"]
    for note, p in prods.items():
        assert p == pytest.approx(ref, rel=0.02), f"{note}: f*Ct = {p:.3g} vs A {ref:.3g}"
    fa = master_freq(run_siggen(note="A"))
    assert fa == pytest.approx(1760.0, rel=0.01)


def test_bus_law_octave_doubling():
    """f is linear in (bias - vbus): one KLM-62D bus octave (doubling of
    (rail - bus)) doubles f. The span=1.0 calibration makes the first octave
    above neutral exact; +-2%-scale residuals further out are Q11's Vbe
    log-bend - the term the bus's own LINEALITY bend compensates at
    instrument level."""
    f = {n: master_freq(run_siggen(note="A", vbus=bus_octave(n))) for n in [-1, 0, 1, 2, 3]}
    assert f[1] / f[0] == pytest.approx(2.0, rel=0.005)
    assert f[-1] / f[0] == pytest.approx(0.5, rel=0.02)
    assert f[2] / f[0] == pytest.approx(4.0, rel=0.03)
    assert f[3] / f[0] == pytest.approx(8.0, rel=0.06)


def test_divider_ratios():
    """The behavioral MM5824 stand-in (XSPICE toggle chain) divides by
    exactly 2/4/8; asserts the netlist construction the DSP relies on."""
    res = run_siggen(note="A")
    fm = master_freq(res)
    for name, ratio in [("vq1", 2.0), ("vq2", 4.0), ("vq3", 8.0)]:
        f = crossing_freq(res["t"], res[name])
        assert fm / f == pytest.approx(ratio, rel=0.002), f"{name}: {fm / f:.4f}"


def test_staircase_levels_match_ladder():
    """The summing node shows the 5 predicted conductance-weighted levels of
    the row-3 100k/200k/200k ladder within 40 mV."""
    res = run_siggen(note="A")
    expected = np.unique(np.round(ladder_levels(), 6))
    ns = res["ns"]
    step = np.diff(expected).mean()
    for lv in expected:
        near = ns[np.abs(ns - lv) < 0.4 * step]
        assert len(near) > 50, f"no plateau near {lv:.3f} V"
        assert np.median(near) == pytest.approx(lv, abs=0.04)


def test_saw_mode_follows_staircase():
    """Sawtooth rails (WFR 14.83 V clamp): the PNP saturates against the
    load and the output follows the staircase - 5 distinct rising levels,
    positively correlated with the summing node, never near 0."""
    res = run_siggen(note="A", **dict(zip(("wfd", "wfr"), RAILS["saw"])))
    nout, ns = res["nout"], res["ns"]
    assert nout.min() > 4.0
    assert np.corrcoef(ns, nout)[0, 1] > 0.99
    assert nout.max() - nout.min() == pytest.approx(4.06, abs=0.3)


@pytest.mark.parametrize(
    ("sel", "duty", "on_level"),
    [("p-wide", 1 / 8, 5.55), ("p-mid", 3 / 8, 6.87), ("p-nar", 7 / 8, 8.94)],
)
def test_pulse_modes_slice_the_staircase(sel, duty, on_level):
    """Pulse rails: D31 pins the emitter at wfd - ~0.585 V and the cell is a
    comparator - output high for the staircase slots below it (the deep
    saturation's C-B feedback flattens the on-level to 0.82*(wfd-vd-vsat)).
    The three panel widths slice at 1/8, 3/8, 7/8 of the cycle with the
    vsq/vmid staircase placement (flagged for hardware measurement)."""
    wfd, wfr = RAILS[sel]
    res = run_siggen(note="A", wfd=wfd, wfr=wfr)
    assert duty_high(res) == pytest.approx(duty, abs=0.02)
    nout = res["nout"]
    assert nout.min() == pytest.approx(0.0, abs=0.05)
    on = np.median(nout[nout > 0.5 * nout.max()])
    assert on == pytest.approx(on_level, abs=0.1)


def test_pwm_duty_continuous_in_wfd():
    """The PWM path (WFD 6.7-11.9 V per the KLM-63 law) sweeps the slicing
    threshold across essentially the whole staircase: at the bottom of the
    range only the lowest slot (weakly) conducts, at the top the output is
    high the full cycle, monotone in between."""
    duties = []
    for wfd in [6.7, 7.37, 8.99, 9.95, 11.52, 11.9]:
        res = run_siggen(note="A", wfd=wfd, wfr=0.0)
        duties.append(duty_high(res, mid=0.5 * max(res["nout"].max(), 1.0)))
    assert duties[0] <= 1 / 8 + 0.02
    assert duties[-1] > 0.99
    assert np.all(np.diff(duties) >= 0), duties


def test_triangle_mode_folds():
    """Triangle rails (TRI ADJ on WFR, WFD low): steps above the WFR-set
    threshold leave saturation for the inverting linear region - the top of
    the staircase folds DOWN (output at the top slot far below the peak),
    unlike saw where the top slot IS the peak; lowering TRI ADJ moves the
    fold deeper into the cycle."""
    for sel, folded_slots in [("tri-mid", 1), ("tri-lo", 5)]:
        wfd, wfr = RAILS[sel]
        res = run_siggen(note="A", wfd=wfd, wfr=wfr)
        nout, ns = res["nout"], res["ns"]
        top = nout[ns > ns.max() - 0.3]  # output during the top staircase slot
        assert np.median(top) < nout.max() - 3.0, sel
        low = float(np.mean(nout < 1.0))
        assert low == pytest.approx(folded_slots / 8, abs=0.08), sel
    # tri-hi backs the fold out past the staircase top: pure follower (saw-like)
    res = run_siggen(note="A", **dict(zip(("wfd", "wfr"), RAILS["tri-hi"])))
    assert res["nout"].min() > 4.0


def test_shaper_output_bounded():
    """Output pinned between PNP cutoff (0 V into the 100k load) and the
    saturated follow of the top staircase step at every panel selection."""
    for sel, (wfd, wfr) in RAILS.items():
        nout = run_siggen(note="A", wfd=wfd, wfr=wfr)["nout"]
        assert nout.min() > -0.01, sel
        assert nout.max() < 9.6, sel
