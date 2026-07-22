"""Compare dsp/siggen.dsp against the SPICE reference (klm64-siggen.cir).

A free-running tone source does not fit the impulse-response harness used by
the resonator, so the comparison is:
  (a) oscillation frequency at matched (note, temperament-bus) points -- DSP
      frequency measured from the once-per-staircase-cycle large downward
      step (the wrap of the rising staircase in saw mode), SPICE from ramp
      crossings (tests/test_siggen_spice.py helpers);
  (b) steady-state waveform shape per panel rail selection: Fourier
      coefficients of one staircase cycle of the shaper output, integrated
      identically on both sides (magnitudes are start-phase invariant, so
      the free-running phase difference between the two simulators drops
      out).
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from tests.test_siggen_spice import (
    CHART,
    RAILS,
    bus_octave,
    master_freq,
    run_siggen,
    staircase_harmonics,
)
from tests.test_dsp_vs_spice import REPO, build_driver

FS = 48_000
N = 1 << 17

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)


@pytest.fixture(scope="session")
def siggen_bin():
    return build_driver(REPO / "dsp" / "siggen.dsp", "siggen_ir")


def render(binary, *args: str, n: int = N) -> np.ndarray:
    out = subprocess.run(
        [str(binary), f"n={n}", f"fs={FS}", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return np.array(out.split(), dtype=float)


def dsp_master_freq(x: np.ndarray, div: int = 8) -> float:
    """Master frequency from the staircase cycle's single large downward
    step (saw: the wrap of the rising staircase; pulse/triangle: the
    comparator/fold drop). Deterministic phase increment means the
    +-1-sample edge quantization averages out over the ~hundreds of cycles
    rendered."""
    jumps = np.where(np.diff(x) < -1.0)[0]
    per = np.diff(jumps).mean() / FS
    return div / per


# ---------------------------------------------------------------------------
# Python replica of the DSP's closed forms (same constants as dsp/siggen.dsp)

VT = 0.02585
VBQ = -0.039159
RR = 71.9e3
DVQ = 6.62863
KDISQ = 4954.90
IMIN = 2e-8
CT_PF = [1547.0, 1470.0, 1380.0, 1300.0, 1220.0, 1150.0, 1100.0, 1033.0, 970.0, 920.0, 867.0, 820.0]


def law_freq(note: str, vbus: float) -> float:
    ct = CT_PF[list(CHART).index(note)] * 1e-12
    i = max((VBQ - 0.545 - vbus) / RR, IMIN)
    for _ in range(5):
        i = max((VBQ - VT * np.log(max(i, IMIN) / 1e-14) - vbus) / RR, IMIN)
    return 1.0 / (DVQ * ct / i + KDISQ * ct)


def cell_out(ns, wfd: float, wfr: float) -> np.ndarray:
    """Python replica of the dsp/siggen.dsp shaper cell (same constants)."""
    ns = np.asarray(ns, float)
    nvt, vd, vsat, vebsat, voff, wsm = 1.7 * VT, 0.585, 0.02, 0.66, 0.041, 0.18
    beta = 1.78e-3
    af = beta * 22e3
    u = ns + 1.5
    x = (-1.0 + np.sqrt(1.0 + 4.0 * af * (u + 14.9))) / (2.0 * af)
    s = u - x

    def idio(e):
        return 2.5e-9 * np.exp(np.minimum((wfd - e) / nvt, 26.0))

    def i18(e):
        return (wfr - e) / 18e3

    e = np.maximum(s + vebsat, wfd - vd)
    for _ in range(6):
        xj = np.maximum(u - (e - vebsat), 0.0)
        f = (
            i18(e)
            + idio(e)
            - np.maximum(e - vsat, 0.0) / 122e3
            - ((e - vebsat) + 14.9) / 22e3
            + beta * xj**2
        )
        df = -1 / 18e3 - idio(e) / nvt - 1 / 122e3 - 1 / 22e3 - 2 * beta * xj
        e = e - f / df
    nsat = 0.8197 * (e - vsat)
    ea = s + 0.62
    for _ in range(8):
        ea = 0.5 * (ea + s + VT * np.log(np.maximum(i18(ea), 1e-9) / 1e-14))
    ea = np.maximum(ea, wfd - vd)
    isrc = 0.9967 * (np.maximum(i18(ea), 0.0) + idio(ea))
    iexp = 1e-14 * np.exp(np.minimum((ea - s + voff) / VT, 26.0))
    nact = 100e3 * np.minimum(iexp, isrc)
    lo, hi = np.minimum(nsat, nact), np.maximum(nsat, nact)
    return np.maximum(lo - wsm * np.log1p(np.exp(-(hi - lo) / wsm)), 0.0)


ROW_WEIGHTS = [[1.0], [1.0, 1.0], [2.0, 1.0, 1.0], [3.9, 1.95, 1.0, 1.0]]


def row_staircase(oct_: int, vsq: float = 5.2, vmid: float = 7.45) -> np.ndarray:
    """Summing-node level per staircase slot for octave row oct_ (own tap =
    highest bit; re-read ladder pools)."""
    w = ROW_WEIGHTS[oct_]
    n = np.arange(2 ** (oct_ + 1))
    bits = [((n >> (len(w) - 1 - i)) & 1) - 0.5 for i in range(len(w))]
    return vmid + vsq * sum(wi * bi for wi, bi in zip(w, bits)) / sum(w)


# frequency tolerance: the DSP law reproduces the SPICE grid to 0.37% max
# (fit residual, dsp/siggen.dsp header); DSP edge quantization and the
# SPICE trip resolution add ~0.1%. 0.6% ~= 10 cents.
FREQ_RTOL = 0.006


@pytest.mark.parametrize(
    ("note", "boct"),
    [("A", 0), ("A", -1), ("A", 1), ("A", 2), ("F", 0), ("C", 0), ("E", 0), ("E", 2)],
)
def test_dsp_frequency_matches_spice(siggen_bin, note, boct):
    vbus = bus_octave(boct)
    fm_spice = master_freq(run_siggen(note=note, vbus=vbus))
    x = render(siggen_bin, f"note={list(CHART).index(note)}", f"cv={vbus}")
    fm_dsp = dsp_master_freq(x[FS:])  # discard first second (settle)
    assert fm_dsp == pytest.approx(fm_spice, rel=FREQ_RTOL), (
        f"{note} bus={vbus:.3f}: DSP {fm_dsp:.2f} Hz vs SPICE {fm_spice:.2f} Hz"
    )


# harmonic tolerance: the closed-form cell matches the transistor-level
# SPICE cell within 68 mV at the staircase levels (DC-sweep fit); DSP edge
# sampling adds a few tenths of a dB. Only harmonics within 26 dB of the
# strongest are compared (below that the staircase's near-null harmonics
# are dominated by edge effects).
HARM_TOL_DB = 1.5
HARM_FLOOR_DB = -26.0
DC_TOL_V = 0.12


@pytest.mark.parametrize("sel", ["saw", "tri-mid", "tri-lo", "p-mid", "p-nar", "pwm"])
def test_dsp_waveform_harmonics_match_spice(siggen_bin, sel):
    wfd, wfr = RAILS[sel]
    res = run_siggen(note="A", wfd=wfd, wfr=wfr)
    fm = master_freq(res)
    hs = staircase_harmonics(res["t"], res["nout"], 8.0 / fm)

    # each side is projected on ITS OWN fundamental: the 0.1%-scale frequency
    # mismatch between the simulators would otherwise decohere the Fourier
    # integral over the DSP's ~hundreds of rendered cycles
    x = render(siggen_bin, "note=4", f"wfd={wfd}", f"wfr={wfr}")[FS:]
    t = np.arange(len(x)) / FS
    hd = staircase_harmonics(t, x, 8.0 / dsp_master_freq(x))

    assert abs(hd[0].real - hs[0].real) < DC_TOL_V, "DC"
    ref = np.abs(hs[1:]).max()
    for k in range(1, 8):
        if np.abs(hs[k]) < ref * 10 ** (HARM_FLOOR_DB / 20):
            continue
        db = 20 * np.log10(np.abs(hd[k]) / np.abs(hs[k]))
        assert abs(db) < HARM_TOL_DB, f"{sel} harmonic {k}: {db:+.2f} dB"


def test_dsp_octave_rows(siggen_bin):
    """Octave row k runs its staircase at fm/2^(k+1) and reproduces exactly
    the ladder levels available to that row (through the same closed-form
    cell; saw rails, so every level survives to the output)."""
    fm = master_freq(run_siggen(note="A"))
    for oct_ in range(4):
        x = render(siggen_bin, "note=4", f"octave={oct_}")[FS:]
        expected = np.unique(np.round(cell_out(row_staircase(oct_), *RAILS["saw"]), 3))
        got = np.unique(np.round(x, 3))
        assert np.allclose(got, expected, atol=2e-3), f"octave {oct_}: {got} vs {expected}"
        jumps = np.where(np.diff(x) < -1.0)[0]
        per = np.diff(jumps).mean() / FS
        assert 2 ** (oct_ + 1) / per == pytest.approx(fm, rel=0.01), f"octave {oct_}"


def test_dsp_pwm_duty_monotone(siggen_bin):
    """The DSP cell slices the staircase exactly like SPICE across the PWM
    span: duty(high) monotone from <=1/8 (bottom sliver) to 1."""
    duties = []
    for wfd in [6.7, 8.99, 9.95, 11.52, 11.9]:
        x = render(siggen_bin, "note=4", f"wfd={wfd}", "wfr=0.0")[FS:]
        duties.append(float(np.mean(x > 0.5 * max(x.max(), 1.0))))
    assert duties[0] <= 1 / 8 + 0.02
    assert duties[-1] > 0.99
    assert np.all(np.diff(duties) >= 0), duties


def test_dsp_law_replica_matches_binary(siggen_bin):
    """The Python law replica used to reason about the DSP is the DSP: the
    rendered oscillation frequency matches law_freq to the edge-quantization
    floor (guards the shared constants from drifting apart)."""
    for note, vbus in [("A", -1.62), ("E", -4.83)]:
        x = render(siggen_bin, f"note={list(CHART).index(note)}", f"cv={vbus}")[FS:]
        assert dsp_master_freq(x) == pytest.approx(law_freq(note, vbus), rel=0.002)
