"""Faust wave-form-control (dsp/wavectl.dsp) vs SPICE
(netlists/klm63-wavectl.cir).

The board is a DC machine (two smoothed control rails), so the comparison
is a DC grid plus transients for the two dynamic paths:

  - DC: every panel selection (with TRI ADJ and PWM sub-grids) rendered to
    settle (>10 release taus), final sample vs the ngspice op via the
    cached runner in test_wavectl_spice
  - PWM step: pwm_dc 0 -> 5V mid-render vs the netlist's dynp hook (same
    zener-chain RC, ~1 ms)
  - release: wave pulse-wide -> saw mid-render vs the netlist's dyn hook
    (both decay node Y through R113 1M, 47 ms)

The driver prints one channel, so the DSP exposes a probe control
(0 = WFR pin 12, 1 = WFD pin 13)."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, build_driver, render
from tests.test_wavectl_spice import dyn_tran, fitted_tau, op_point

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

# 60 mV: the DSP bakes SPICE-anchored constants, so the residual is its
# affine fits (TRI ADJ level vs wiper, PWM law vs the zener-chain solve),
# both <= ~40 mV over the tested grids
DC_TOL_V = 0.06
N_SETTLE = 3 * FS // 2  # 1.5 s >> 10 * 47 ms release tau


@pytest.fixture(scope="module")
def wavectl_bin() -> Path:
    return build_driver(REPO / "dsp" / "wavectl.dsp", "wavectl_ir")


def dc_pair(binary: Path, *args: str) -> tuple[float, float]:
    """(WFR, WFD) settled levels for one control setting."""
    wfr = render(binary, "probe=0", *args, n=N_SETTLE)[-1]
    wfd = render(binary, "probe=1", *args, n=N_SETTLE)[-1]
    return float(wfr), float(wfd)


CASES = [
    (("wave=0", "tri_adj=0.0"), dict(sel_tri=1, tri_adj=0.0)),
    (("wave=0", "tri_adj=0.5"), dict(sel_tri=1, tri_adj=0.5)),
    (("wave=0", "tri_adj=1.0"), dict(sel_tri=1, tri_adj=1.0)),
    (("wave=1",), dict(sel_saw=1)),
    (("wave=2",), dict(sel_w=1)),
    (("wave=3",), dict(sel_m=1)),
    (("wave=4",), dict(sel_n=1)),
    (("wave=5",), dict(sel_x=1)),
    (("wave=5", "pwm_on=1", "pwm_dc=-5"), dict(sel_x=1, pwm_on=1, vpwm=-5)),
    (("wave=5", "pwm_on=1", "pwm_dc=0"), dict(sel_x=1, pwm_on=1, vpwm=0)),
    (("wave=5", "pwm_on=1", "pwm_dc=5"), dict(sel_x=1, pwm_on=1, vpwm=5)),
    (("wave=5", "pwm_on=1", "pwm_dc=12"), dict(sel_x=1, pwm_on=1, vpwm=12)),
]


@pytest.mark.parametrize("dsp_args,spice_params", CASES, ids=[" ".join(c[0]) for c in CASES])
def test_dc_levels_match_spice(wavectl_bin, dsp_args, spice_params):
    wfr_d, wfd_d = dc_pair(wavectl_bin, *dsp_args)
    wfr_s, wfd_s = op_point(**spice_params)
    assert wfr_d == pytest.approx(wfr_s, abs=DC_TOL_V)
    assert wfd_d == pytest.approx(wfd_s, abs=DC_TOL_V)


def test_pwm_step_tau_matches_spice(wavectl_bin):
    """Step pwm_dc 0 -> 5V; both engines relax through the same RC. 35%:
    the DSP uses a fixed 21.6k source resistance where SPICE's diode/zener
    incremental resistances move with the level."""
    step_at = FS // 2
    y = render(wavectl_bin, "probe=1", "wave=5", "pwm_on=1", f"step:pwm_dc={step_at}:5", n=FS)
    t = np.arange(len(y)) / FS - step_at / FS
    seg = (t > 0) & (t < 8e-3)
    tau_d = fitted_tau(t[seg], y[seg], float(y[-1]))

    ts, wfd_s = dyn_tran("dynp")
    final_s = wfd_s[(ts > 50e-3) & (ts < 60e-3)].mean()
    segs = (ts > 1e-3) & (ts < 8e-3)
    tau_s = fitted_tau(ts[segs], wfd_s[segs], final_s)

    assert float(y[-1]) == pytest.approx(final_s, abs=DC_TOL_V)
    assert tau_d == pytest.approx(tau_s, rel=0.35)


def test_release_tau_matches_spice(wavectl_bin):
    """Deselect the wide pulse (-> saw): WFD decays through R113 1M in
    both engines (the DSP's tauFall vs the netlist's 46.9 ms fit)."""
    step_at = FS // 2
    y = render(wavectl_bin, "probe=1", "wave=2", f"step:wave={step_at}:1", n=3 * FS)
    t = np.arange(len(y)) / FS - step_at / FS
    seg = t > 0
    tau_d = fitted_tau(t[seg], y[seg], 0.0)

    ts, wfd_s = dyn_tran("dyn")
    segs = ts > 61.5e-3
    tau_s = fitted_tau(ts[segs], wfd_s[segs], 0.0)

    assert tau_d == pytest.approx(tau_s, rel=0.1)
