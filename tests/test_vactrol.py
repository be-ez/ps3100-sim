"""Vactrol (P873-class photocoupler) dynamics tests (plan Phase 3b).

Model under test: dsp/vactrol.dsp - two-pole asymmetric photoconductor:
a shared generation pole plus two carrier populations with fixed-tau
light-driven attack and bimolecular dark decay (tau = k/g grows as the
cell conductance falls, giving the CdS long-memory tail). Provenance and
references are in the dsp file header.

Rendering uses the impulse-driver helpers from tests/test_dsp_vs_spice.py
(the vactrol process outputs R/rmax for the current cv slider value).
"""

import shutil

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, REPO, build_driver, render

RMIN, RMAX = 1e3, 1e6  # keep in sync with dsp/vactrol.dsp
GMIN = 1.0 / RMAX

# decay trace timing: settle lit, then step dark and record the recovery
DECAY_SETTLE_S = 0.25
DECAY_TAIL_S = 5.25

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None,
    reason="faust or c++ not installed",
)


def cv2r(cv: float) -> float:
    return RMAX * (RMIN / RMAX) ** cv


@pytest.fixture(scope="module")
def vactrol_bin():
    return build_driver(REPO / "dsp" / "vactrol.dsp", "vactrol_step")


@pytest.fixture(scope="module")
def attack_trace(vactrol_bin) -> np.ndarray:
    """Resistance vs time, powering up dark with cv=1 (full attack)."""
    return render(vactrol_bin, "cv=1.0", n=FS // 2) * RMAX


@pytest.fixture(scope="module")
def decay_trace(vactrol_bin) -> np.ndarray:
    """Resistance vs time after a lit->dark step, from the step onward."""
    n = int((DECAY_SETTLE_S + DECAY_TAIL_S) * FS)
    step = int(DECAY_SETTLE_S * FS)
    r = render(vactrol_bin, "cv=1.0", f"step:cv={step}:0.0", n=n) * RMAX
    return r[step:]


def test_powers_up_dark_attack_fast_decay_slow(vactrol_bin):
    # ported from tests/test_dsp_vs_spice.py: cv=1 (lit) from power-up,
    # step to cv=0 (dark) at 1 s; output is R/rmax
    n = 2 * FS
    step = FS
    r = render(vactrol_bin, "cv=1.0", f"step:cv={step}:0.0", n=n) * RMAX

    # powers up dark
    assert r[0] > 0.9e6
    # attack: resistance collapses to ~rmin within tens of ms
    assert r[int(0.05 * FS)] < 5e3
    # asymmetry: time to cross the halfway resistance is far longer going
    # dark (decay) than lighting up (attack)
    half = 0.5e6
    t_attack = np.argmax(r < half) / FS
    t_decay = (np.argmax(r[step:] > half)) / FS
    assert t_decay > 10 * t_attack, (
        f"attack {t_attack * 1e3:.1f} ms vs decay {t_decay * 1e3:.1f} ms"
    )
    # rises monotonically toward rmax but is nowhere near settled after 1 s
    tail = r[step + 100 :: 1000]
    assert np.all(np.diff(tail) > 0)
    assert 0.3e6 < r[-1] < 0.99e6


def test_attack_time_to_90pct(attack_trace):
    # measured in conductance (the physical carrier density the light
    # drives); expect ms-scale: generation pole + attack pole ~= 10 ms
    g = 1.0 / attack_trace
    g_final = g[-1]
    assert abs(g_final - 1.0 / RMIN) / (1.0 / RMIN) < 0.02
    t90 = np.argmax(g >= GMIN + 0.9 * (g_final - GMIN)) / FS
    assert 0.002 < t90 < 0.030, f"attack t90 = {t90 * 1e3:.2f} ms"


def test_decay_time_to_90pct(decay_trace):
    # dark recovery is orders of magnitude slower than attack: the slow
    # population's bimolecular tail puts t90 in the 100ms..seconds range
    r1 = decay_trace[0]
    assert r1 < 2 * RMIN  # started fully lit
    thresh = r1 + 0.9 * (RMAX - r1)
    idx = int(np.argmax(decay_trace >= thresh))
    assert idx > 0, "never reached 90% of the dark resistance"
    t90 = idx / FS
    assert 0.1 < t90 < 4.5, f"decay t90 = {t90:.2f} s"
    # and it does eventually settle essentially dark
    assert decay_trace[-1] > 0.95 * RMAX


def test_decay_slows_as_resistance_rises(decay_trace):
    # memory effect: tau = k/g grows as conductance falls, so successive
    # equal resistance intervals take progressively longer to traverse
    levels = [100e3, 200e3, 300e3, 400e3, 500e3]
    times = np.array([np.argmax(decay_trace >= lv) for lv in levels]) / FS
    assert np.all(times[1:] > 0)
    dts = np.diff(times)
    assert np.all(dts[1:] > 1.05 * dts[:-1]), f"interval times not increasing: {dts}"


def test_no_overshoot_or_oscillation(attack_trace, decay_trace, vactrol_bin):
    # cascaded monotone poles: each phase must be one-directional
    slack = 1e-3  # ohms; double-rounding headroom on a 1e3..1e6 scale
    assert np.all(np.diff(attack_trace) < slack), "attack not monotone"
    assert np.all(np.diff(decay_trace) > -slack), "decay not monotone"
    # never leaves the physical resistance range
    for tr in (attack_trace, decay_trace):
        assert tr.min() > 0.999 * RMIN and tr.max() < 1.001 * RMAX
    # partial step 0.25 -> 0.75 stays bracketed by the two endpoints and
    # lands on the steady-state law (no ringing past the target)
    step = FS // 2
    r = render(vactrol_bin, "cv=0.25", f"step:cv={step}:0.75", n=FS) * RMAX
    seg = r[step:]
    assert np.all(np.diff(seg) < slack)
    assert seg.min() > 0.98 * cv2r(0.75)
    assert seg.max() < 1.001 * r[step - 1]
    assert abs(seg[-1] - cv2r(0.75)) / cv2r(0.75) < 0.02


@pytest.mark.parametrize("cv", [0.25, 0.5, 1.0])
def test_steady_state_matches_cv_law(vactrol_bin, cv):
    # the dynamics must not disturb the exponential cv->R law the CV drive
    # hardware sets (page-0004: ~1.8 V/oct exponential converter)
    r = render(vactrol_bin, f"cv={cv}", n=FS // 2) * RMAX
    assert abs(r[-1] - cv2r(cv)) / cv2r(cv) < 0.02
