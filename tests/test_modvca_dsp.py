"""DSP-vs-SPICE referee for the KLM-63 MOD-VCA + MG2 model (dsp/modvca.dsp).

Same structure as the resonator's DSP validation: ngspice runs of
netlists/klm63-modvca-mg2.cir (via tests/test_modvca_spice.py's cached
helpers) are the reference, the Faust model is rendered offline with
tests/impulse_driver.cpp.

The VCA fits the static harness: the control law (pot -> photocell R) and
the audio-path gain are compared at fixed control settings, with the vactrol
dynamics bypassed exactly like dsp/resonator.dsp's test hook (the dynamics
themselves are validated in tests/test_vactrol.py). The free-running MG2
LFO cannot be driven to a fixed operating point, so its frequency, amplitude
and triangle shape are compared against SPICE transients instead.
"""

import shutil

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, REPO, build_driver, render
from tests.test_modvca_spice import (
    cv_law_grid,
    measure_freq,
    mg2_transient,
    resample,
    vca_ac,
    vca_gain_db,
)

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)


@pytest.fixture(scope="module")
def modvca_bin():
    return build_driver(REPO / "dsp" / "modvca.dsp", "modvca_ir")


def dsp_static_r(modvca_bin, pos: float) -> float:
    """Static drive-law resistance (probe=1 prints rTarget/rmax, memoryless)."""
    out = render(modvca_bin, "probe=1", f"vca_cv={pos}", n=8)
    return float(out[-1]) * 1e6


def test_dsp_static_law_matches_spice(modvca_bin):
    """The Faust drive law (unrolled-Newton BJT solve + P873 power law) vs
    the ngspice DC sweep, compared as VCA gain error. 0.15 dB bounds the
    residual of the simplified solve vs full Gummel-Poon (worst ~0.09 dB at
    the subthreshold knee near the bottom of the pot, where the audio sits
    at ~-40 dB anyway)."""
    law = cv_law_grid()
    for pos in np.arange(0.0, 1.001, 0.05):
        r_dsp = dsp_static_r(modvca_bin, round(float(pos), 3))
        r_sp = float(np.interp(pos, law["pos"], law["rldr"]))
        err = 20 * np.log10((r_sp + 2.2e3) / (r_dsp + 2.2e3))
        assert abs(err) < 0.15, f"pos={pos:.2f}: {r_dsp:.0f} vs {r_sp:.0f} ({err:+.3f} dB)"


@pytest.mark.parametrize("pos", [0.1, 0.5, 0.9])
def test_dsp_vca_gain_matches_spice_ac(modvca_bin, pos):
    """Audio path at a fixed LDR resistance (bypass hook): DSP gain vs the
    SPICE AC magnitude at 1 kHz. 0.05 dB covers the netlist's 470R/1M
    output-load divider, which the DSP intentionally omits (high-Z bus)."""
    law = cv_law_grid()
    rsig = float(np.interp(pos, law["pos"], law["rldr"]))
    ac = vca_ac(rsig)
    spice_db = float(np.interp(1e3, ac["freq"], ac["db"]))
    ir = render(modvca_bin, "bypass_vactrol=1", f"rldr={rsig}", n=16)
    # flat memoryless path: the impulse response is a single scaled impulse
    dsp_db = 20 * np.log10(abs(ir[0]))
    assert dsp_db == pytest.approx(spice_db, abs=0.05)
    assert dsp_db == pytest.approx(vca_gain_db(rsig), abs=0.05)


def test_dsp_dynamic_r_settles_to_static_law(modvca_bin):
    """The vactrol dynamics (dsp/vactrol.dsp, driven through the exact
    inverse of its cv2r law) must settle to the static drive-law resistance;
    2% after 1 s at a bright setting (attack t90 ~ 10 ms)."""
    r_static = dsp_static_r(modvca_bin, 0.8)
    out = render(modvca_bin, "probe=2", "vca_cv=0.8", n=FS)
    assert float(out[-1]) * 1e6 == pytest.approx(r_static, rel=0.02)


@pytest.mark.parametrize("rate,tstop,step", [(0.1, 8.0, 1e-3), (1.0, 3.0, 0.5e-3)])
def test_dsp_mg2_matches_spice(modvca_bin, rate, tstop, step):
    """Free-running MG2: DSP triangle vs SPICE transient -- frequency (2%),
    pin-27 amplitude (3%, covers the deck's 10R opamp output resistance in
    the R221/R222 pad), crest factor and duty (triangle shape)."""
    sp = mg2_transient(rate, tstop, step)
    f_sp = measure_freq(sp["t"], sp["tri"])
    p27 = resample(sp["t"], sp["p27"])

    n = int(tstop * FS)
    y = render(modvca_bin, "probe=3", f"mg2_rate={rate}", n=n)
    y = y[FS:]  # settle (phase alignment is not compared)
    cross = np.where((y[:-1] < 0) & (y[1:] >= 0))[0]
    assert len(cross) >= 3
    f_dsp = FS / np.diff(cross).mean()

    assert f_dsp == pytest.approx(f_sp, rel=0.02)
    assert y.max() == pytest.approx(p27.max(), rel=0.03)
    assert -y.min() == pytest.approx(-p27.min(), rel=0.03)
    crest_dsp = np.abs(y).max() / np.sqrt((y**2).mean())
    crest_sp = np.abs(p27).max() / np.sqrt((p27**2).mean())
    assert crest_dsp == pytest.approx(crest_sp, rel=0.03)
    duty_dsp = (np.diff(y) > 0).mean()
    duty_sp = (np.diff(resample(sp["t"], sp["tri"])) > 0).mean()
    assert duty_dsp == pytest.approx(duty_sp, abs=0.03)
