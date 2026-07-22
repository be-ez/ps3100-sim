"""Compare the KLM-76 Voltage Processor Faust model (dsp/vp.dsp) against the
SPICE reference (netlists/klm76-vp.cir): DC transfer grid over knob x input
(both channels, shared-bus coupling included), small-signal frequency
response at several knob settings, the 1 ms lag transient on the
non-inverting path, clipping, and channel/monitor plumbing."""

import shutil

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, REPO, build_driver, dsp_response_db, render
from tests.test_vp_spice import VCLIP, j, run_vp

VP_DSP = REPO / "dsp" / "vp.dsp"

# DC: identical closed-form algebra on both sides; residual is the tanh
# op-amp linearization (1e-6-ish) plus sweep-grid exactness
DC_TOL_V = 2e-3
# AC: bilinear one-pole vs analog pole at 159 Hz. Within 15 dB of the
# passband the match is numerical; deeper (only reached at g=1, where the
# 159 Hz lag is the whole transfer) the bilinear zero at Nyquist droops the
# tail below the analog -20 dB/dec: 0.26 dB at 5 kHz (-30 dB absolute)
# growing to 1.24 dB at 20 kHz (-42 dB absolute) - a discretization cause
# confined to the deep rolloff of a CV path, not a modeling error
AC_TOL_DB = 0.05
AC_DEEP_TOL_DB = 1.3
AC_DEEP_DB = -15.0
FMIN, FMAX = 20.0, 20e3
TAU_S = 1e-3  # R224 * C204

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)


@pytest.fixture(scope="session")
def vp_bin():
    return build_driver(VP_DSP, "vp_ir")


def dsp_dc(vp_bin, g1, g2, vin1, vin2, monitor=0, n=4096):
    """Settled DC output: sliders set the operating point, the impulse the
    driver injects at t=0 decays through the 1 ms lag well before the end."""
    out = render(
        vp_bin,
        f"knob1={g1}",
        f"knob2={g2}",
        f"vin1={vin1}",
        f"vin2={vin2}",
        f"monitor={monitor}",
        n=n,
    )
    return out[-1]


KNOB_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]
VIN_GRID = [-5.0, -2.5, 0.0, 2.5, 5.0]


@pytest.mark.parametrize("g", KNOB_GRID)
def test_dc_grid_matches_spice(vp_bin, g):
    """Full DC referee: knob x input grid with the second channel counter-
    driven at a fixed point, so the shared -5 V bus coupling is exercised
    on both sides of the comparison."""
    spice = run_vp(ka1=1 - g, kb1=g, ka2=1 - g, kb2=g, vin2=-2.5)
    for vin in VIN_GRID:
        got = dsp_dc(vp_bin, g, g, vin, -2.5)
        assert abs(got - spice["pout1"][j(vin)]) < DC_TOL_V, f"g={g} vin={vin}"
    # channel 2 at its own operating point, read through the monitor tap
    got2 = dsp_dc(vp_bin, g, g, 0.0, -2.5, monitor=1)
    assert abs(got2 - spice["pout2_x"][j(0.0)]) < DC_TOL_V


@pytest.mark.parametrize("g", [1.0, 0.75, 0.5, 0.25, 0.0])
def test_ac_matches_spice(vp_bin, g):
    """Small-signal referee: impulse response (ac=1 strips the DC operating
    point and the clip) against the SPICE AC magnitude. Covers the 159 Hz
    lag at g=1, the flat direct path at g=0, and the -6 dB HPF-like leak at
    knob center."""
    ir = render(vp_bin, f"knob1={g}", f"knob2={g}", "ac=1", n=1 << 15)
    f_dsp, db_dsp = dsp_response_db(ir)
    spice = run_vp(ka1=1 - g, kb1=g, ka2=1 - g, kb2=g)
    f_sp, db_sp = spice["freq"], spice["ac1_db"]
    mask = (f_sp >= FMIN) & (f_sp <= FMAX)
    # compare only above -60 dB of the passband: at knob center the LF gain
    # nulls and both layers dive into numerical noise floors
    ref = db_sp[mask].max()
    mask2 = mask & (db_sp > ref - 60.0)
    dsp_on_f = np.interp(np.log10(f_sp[mask2]), np.log10(f_dsp[1:]), db_dsp[1:])
    err = np.abs(dsp_on_f - db_sp[mask2])
    shallow = db_sp[mask2] > ref + AC_DEEP_DB
    assert err[shallow].max() < AC_TOL_DB, f"g={g}: {err[shallow].max():.3f} dB"
    if (~shallow).any():
        assert err[~shallow].max() < AC_DEEP_TOL_DB, f"g={g} deep: {err[~shallow].max():.3f} dB"


def test_step_lag_time_constant(vp_bin):
    """Non-inverting path (g=1): a vin step lands through the 1 ms R224/C204
    lag; check the 63.2% point. Inverting path (g=0): settles within a
    couple of samples (direct pot tap, no filter in the path)."""
    n = 1 << 13
    at = 1000
    out = render(vp_bin, "knob1=1.0", "knob2=1.0", f"step:vin1={at}:5.0", n=n)
    y0, y1 = out[at - 1], out[-1]
    target = y0 + (1 - np.exp(-1)) * (y1 - y0)
    k = at + np.searchsorted(out[at:], target)
    tau = (k - at) / FS
    assert tau == pytest.approx(TAU_S, rel=0.05)
    inv = render(vp_bin, "knob1=0.0", "knob2=0.0", f"step:vin1={at}:5.0", n=n)
    # the direct path lands within a sample; the ~1% residual settling is
    # the lagged antiphase bus injection fading in (same cause as the SPICE
    # +0.11 dB shelf in test_lag_only_on_noninverting_path)
    jump = inv[at] - inv[at - 1]
    total = inv[-1] - inv[at - 1]
    assert abs(jump) > 0.95 * abs(total)
    assert abs(inv[at + 3] - inv[-1]) < 0.1


def test_clip(vp_bin):
    """House 4558 swing: mixer clip at -13.4 V; the positive side clips in
    the input amp first and lands slightly lower through the pot (SPICE-
    matched exactly, so just window it here)."""
    hi = dsp_dc(vp_bin, 1.0, 1.0, 20.0, 0.0)
    lo = dsp_dc(vp_bin, 1.0, 1.0, -20.0, 0.0)
    assert 13.0 < hi <= VCLIP + 1e-9
    assert lo == pytest.approx(-VCLIP, abs=1e-6)
    spice = run_vp()
    assert abs(hi - spice["pout1"][j(20.0)]) < DC_TOL_V
    assert abs(lo - spice["pout1"][j(-20.0)]) < DC_TOL_V


def test_channels_and_monitor(vp_bin):
    """The two channels are the same network; monitor=1 must tap channel 2
    exactly, including its knob and the bus coupling from channel 1."""
    a = dsp_dc(vp_bin, 0.3, 0.8, 1.5, -4.0, monitor=1)
    b = dsp_dc(vp_bin, 0.8, 0.3, -4.0, 1.5, monitor=0)
    assert a == pytest.approx(b, abs=1e-9)
