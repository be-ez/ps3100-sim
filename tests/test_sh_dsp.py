"""Faust S&H (dsp/sh.dsp) vs SPICE (netlists/klm76-sh.cir), transient method.

A clocked S&H has no meaningful impulse response, so instead of the
impulse-FFT harness both engines are driven with the same deterministic
input at matched clock settings.

Both engines implement the same exponential clock law (the SPICE side as
the traced Q1-Q4 transistor oscillator, the Faust side as the law fitted to
SPICE sweeps of that netlist), so SPICE vpot=8 corresponds to Faust
clock=0.8 (~6.4 Hz).

- 1 V/s ramp: compares mean clock period and every held plateau level
  against 1.1 * ramp(t at sample-pulse end) - i.e. step timing AND sample
  accuracy in one run;
- 2 V DC with the hold-cap bias current cranked to 100 nA (1 V/s): compares
  the hold droop slope between engines.

Faust renders reuse tests/impulse_driver.cpp via the build_driver/render
helpers of tests/test_dsp_vs_spice.py; the deterministic inputs come from
sh.dsp's internal test sources (testmode ramp/sine/dc) because the driver
itself only injects an impulse."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, build_driver, render
from tests.test_sh_spice import (
    GAIN_BUF,
    T_PULSE,
    clock_edges,
    clock_period_theory,
    run_tran,
)

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

# tolerances
PERIOD_RTOL = 0.02  # residual = fit error of the Faust (F0, K) law vs the
#                     transistor-level SPICE oscillator (<0.5% at vpot=8)
LEVEL_TOL_V = 0.005  # held level vs 1.1*ramp(sample instant): dominated by
#                      the 0.5 ms SPICE print step on a 1 V/s ramp
DROOP_RTOL = 0.05


@pytest.fixture(scope="module")
def sh_bin() -> Path:
    return build_driver(REPO / "dsp" / "sh.dsp", "sh_ir")


def render_seconds(binary: Path, seconds: float, *args: str) -> tuple[np.ndarray, np.ndarray]:
    n = int(seconds * FS)
    y = render(binary, *args, n=n)
    return np.arange(n) / FS, y


def step_edges(
    t: np.ndarray, y: np.ndarray, thresh: float, min_gap: float = 0.1, grid: float = 1e-3
) -> np.ndarray:
    """Times of held-value steps: first sample of each |diff| burst. The
    trace is first resampled onto a uniform grid because ngspice output is
    not uniformly spaced - the transistor latch forces us-scale timesteps
    around each sampling instant, which would smear a step over many tiny
    diffs."""
    tg = np.arange(t[0], t[-1], grid)
    yg = np.interp(tg, t, y)
    idx = np.where(np.abs(np.diff(yg)) > thresh)[0]
    edges, last_t = [], -np.inf
    for i in idx:
        if tg[i + 1] - last_t > min_gap:
            edges.append(tg[i + 1])
        last_t = tg[i + 1]
    return np.array(edges)


def plateau_levels(t: np.ndarray, y: np.ndarray, edges: np.ndarray) -> np.ndarray:
    return np.array(
        [np.median(y[(t > e0 + 0.02) & (t < e1 - 0.01)]) for e0, e1 in zip(edges[:-1], edges[1:])]
    )


@pytest.fixture(scope="module")
def ramp_pair(sh_bin):
    """Both engines sampling the same 1 V/s ramp at pot position 0.8."""
    spice = run_tran(vpot=8.0, tstop=4.0, vin_line="Vin in 0 PWL(0 0 20 20)")
    s_edges = step_edges(spice["t"], spice["out"], thresh=0.05)
    ft, fy = render_seconds(sh_bin, 4.0, "testmode=1", "ramp_slope=1", "clock=0.8")
    f_edges = step_edges(ft, fy, thresh=0.05)
    return spice, s_edges, (ft, fy), f_edges


def test_clock_period_matches_spice(ramp_pair):
    """Step timing: mean sample period of both engines agrees (and with the
    shared analytic law)."""
    _, s_edges, _, f_edges = ramp_pair
    t_spice = np.diff(s_edges).mean()
    t_faust = np.diff(f_edges).mean()
    assert t_faust == pytest.approx(t_spice, rel=PERIOD_RTOL)
    assert t_spice == pytest.approx(clock_period_theory(8.0), rel=PERIOD_RTOL)


def test_held_levels_match_spice(ramp_pair):
    """Held levels: every plateau equals 1.1 * ramp(pulse end) in both
    engines (the cap tracks the input for the full ~1.33 ms pulse, so the
    held value is the input at the end of the sampling window)."""
    spice, s_edges, (ft, fy), f_edges = ramp_pair
    for t, y, edges in ((spice["t"], spice["out"], s_edges), (ft, fy, f_edges)):
        levels = plateau_levels(t, y, edges)
        expect = GAIN_BUF * 1.0 * (edges[:-1] + T_PULSE)  # 1 V/s ramp
        assert np.max(np.abs(levels - expect)) < LEVEL_TOL_V
    # per-step increments (1.1 * slope * T_clock) agree across engines
    s_inc = np.diff(plateau_levels(spice["t"], spice["out"], s_edges)).mean()
    f_inc = np.diff(plateau_levels(ft, fy, f_edges)).mean()
    assert f_inc == pytest.approx(s_inc, rel=PERIOD_RTOL)


def test_droop_matches_spice(sh_bin):
    """Hold droop: bias current cranked to 100 nA -> 1 V/s in both engines
    (SPICE ibias param / Faust droop hook), measured as the output slope in
    the middle of one hold interval."""
    ibias, c9 = 100e-9, 0.1e-6
    spice = run_tran(vpot=5.0, tstop=1.6, ibias=ibias, vindc=2.0)
    e0 = clock_edges(spice)[0]
    m = (spice["t"] > e0 + 0.15) & (spice["t"] < e0 + 0.65)
    s_slope = np.polyfit(spice["t"][m], spice["out"][m], 1)[0]

    ft, fy = render_seconds(
        sh_bin, 1.6, "testmode=3", "dc_level=2", "clock=0.5", f"droop={ibias / c9}"
    )
    # Faust samples at t~0; measure inside the first full hold interval
    m = (ft > 0.15) & (ft < 0.65)
    f_slope = np.polyfit(ft[m], fy[m], 1)[0]
    assert f_slope == pytest.approx(s_slope, rel=DROOP_RTOL)
    assert f_slope == pytest.approx(-ibias / c9, rel=DROOP_RTOL)


def test_faust_panel_law_center(sh_bin):
    """Faust-only: pot at center gives the calibrated ~1.2 Hz panel rate."""
    ft, fy = render_seconds(sh_bin, 4.0, "testmode=1", "ramp_slope=1", "clock=0.5")
    edges = step_edges(ft, fy, thresh=0.05)
    assert 1.0 / np.diff(edges).mean() == pytest.approx(1.2, rel=0.05)


def test_faust_min_rate_no_stall(sh_bin):
    """Faust-only: the traced exponential-converter law never stalls - pot
    fully CCW still clocks at f = 1.2*exp(-K/2) ~ 0.073 Hz (the old
    functional Schmitt model spuriously stalled below pot ~0.3)."""
    f_min = 1.2 * np.exp(-5.588 / 2)
    ft, fy = render_seconds(sh_bin, 30.0, "testmode=1", "ramp_slope=1", "clock=0.0")
    edges = step_edges(ft, fy, thresh=0.05, min_gap=1.0)
    assert len(edges) >= 2
    assert 1.0 / np.diff(edges).mean() == pytest.approx(f_min, rel=0.05)
