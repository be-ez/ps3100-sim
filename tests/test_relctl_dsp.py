"""Compare the Faust release-control model (dsp/relctl.dsp) against the
SPICE referee (netlists/klm62d-relctl.cir).

The board is dynamic (C1 slews every full-damp transition), so the
comparison is transient at matched settings: both simulators run the same
panel-switch schedule - ngspice via the deck's PWL-driven switch elements,
the Faust model via the impulse driver's step feature - and the resulting
terminal-voltage waveforms are compared through settled levels and
transition-crossing times.

The DSP starts its C1 state at the HD-grounded condition (B = 0) while
SPICE starts from the settled DC operating point, so every scenario keeps
the switches in state 0 for 0.4 s (>> 5 tau of the ~60 ms lattice RC)
before the step, and levels are measured just before the step and at the
end of the run.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, REPO, build_driver, render
from tests.test_relctl_spice import V_FULL, cross, run_relctl

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

# level agreement: worst measured residual is the saturation-floor /
# conduction-corner softness the piecewise-linear DSP flattens (<0.05 V
# measured); 0.15 V keeps that visible against the 11.6 V scale.
LEVEL_TOL = 0.15
# crossing-time agreement: measured residuals 1..7 % (junction softness
# around the corners is not in the piecewise model); 15 % + 3 ms headroom.
TIME_RTOL = 0.15
TIME_ABS = 3e-3

TSW = 0.4
TSTOP = 1.0


@pytest.fixture(scope="session")
def relctl_bin() -> Path:
    return build_driver(REPO / "dsp" / "relctl.dsp", "relctl_ir")


def render_relctl(relctl_bin, rel0, hd0, rel1, hd1, halfd):
    n = int(TSTOP * FS)
    k = int(TSW * FS)
    args = [f"release={rel0}", f"hd={hd0}", f"halfd={halfd}"]
    if rel1 != rel0:
        args.append(f"step:release={k}:{rel1}")
    if hd1 != hd0:
        args.append(f"step:hd={k}:{hd1}")
    out = render(relctl_bin, *args, n=n)
    return {"t": np.arange(n) / FS, "out": out}


# scenarios: every panel transition class (both slewed Q1 edges, the fast
# Q2 edge, the switch-shorted HD engage) across the VR1 trim range
SCENARIOS = [
    # (rel0, hd0, rel1, hd1, halfd, mid-crossing rising?)
    pytest.param(0, 1, 0, 0, 1.0, False, id="half_to_damped"),
    pytest.param(0, 1, 0, 0, 0.5, False, id="half_to_damped_trim"),
    pytest.param(0, 0, 1, 0, 1.0, True, id="idle_to_release"),
    pytest.param(1, 0, 0, 0, 1.0, False, id="release_to_idle"),
    pytest.param(0, 0, 0, 1, 0.0, True, id="idle_to_half_trim0"),
]


@pytest.mark.parametrize("rel0,hd0,rel1,hd1,halfd,rising", SCENARIOS)
def test_dsp_transition_matches_spice(relctl_bin, rel0, hd0, rel1, hd1, halfd, rising):
    sp = run_relctl(rel0, rel1, TSW, hd0, hd1, TSW, halfd, tstop=TSTOP)
    dp = render_relctl(relctl_bin, rel0, hd0, rel1, hd1, halfd)

    # settled levels immediately before the step and at the end of the run
    for name, mask_fn in [
        ("pre", lambda t: (t > TSW - 0.05) & (t < TSW - 1e-4)),
        ("post", lambda t: t > TSTOP - 0.05),
    ]:
        lv_sp = float(sp["out"][mask_fn(sp["t"])].mean())
        lv_dp = float(dp["out"][mask_fn(dp["t"])].mean())
        assert abs(lv_dp - lv_sp) < LEVEL_TOL, f"{name} level: dsp {lv_dp:.3f} vs spice {lv_sp:.3f}"

    # mid-level crossing time of the transition
    lo = min(sp["out"][-1], sp["out"][sp["t"] < TSW][-1])
    hi = max(sp["out"][-1], sp["out"][sp["t"] < TSW][-1])
    mid = 0.5 * (lo + hi)
    t_sp = cross(sp["t"], sp["out"], mid, rising=rising) - TSW
    t_dp = cross(dp["t"], dp["out"], mid, rising=rising) - TSW
    assert abs(t_dp - t_sp) < TIME_RTOL * abs(t_sp) + TIME_ABS, (
        f"mid-crossing: dsp {t_dp * 1e3:.2f} ms vs spice {t_sp * 1e3:.2f} ms"
    )


def test_dsp_release_disengage_two_stage(relctl_bin):
    """The release->idle edge must keep its two-stage shape: a one-sample
    Q2 drop to the half-damp shelf, then the slewed Q1 pull to the floor."""
    dp = render_relctl(relctl_bin, 1, 0, 0, 0, 1.0)
    t, v = dp["t"], dp["out"]
    assert cross(t, v, 9.0, rising=False) - TSW < 1e-3
    t1v = cross(t, v, 1.0, rising=False) - TSW
    sp = run_relctl(1, 0, TSW, 0, 0, TSW, 1.0, tstop=TSTOP)
    t1v_sp = cross(sp["t"], sp["out"], 1.0, rising=False) - TSW
    assert abs(t1v - t1v_sp) < TIME_RTOL * t1v_sp + TIME_ABS


@pytest.mark.parametrize("halfd", [0.0, 0.5, 1.0])
def test_dsp_half_level_grid(relctl_bin, halfd):
    """Static half-damp level across the VR1 trim grid."""
    sp = run_relctl(0, 0, 1.0, 1, 1, 1.0, halfd, tstop=0.3)
    dp = render_relctl(relctl_bin, 0, 1, 0, 1, halfd)
    assert abs(dp["out"][-1] - sp["out"][-1]) < LEVEL_TOL, f"halfd={halfd}"


def test_dsp_full_release_level(relctl_bin):
    dp = render_relctl(relctl_bin, 1, 0, 1, 0, 1.0)
    assert dp["out"][-1] == pytest.approx(V_FULL, abs=LEVEL_TOL)
