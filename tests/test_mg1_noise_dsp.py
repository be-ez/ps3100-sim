"""Compare dsp/mg1_noise.dsp against the KLM-63 SPICE deck (SPICE is the
referee). Free-running LFOs and stochastic noise don't fit the shared
impulse-comparison harness, so:

  - LFO: frequency and waveform levels/shape metrics at matched rate
    settings vs ngspice transient (phase is not comparable)
  - noise: the pink shaping filter's magnitude response (dsp outsel=6 routes
    the audio INPUT through the filter -> impulse/FFT) vs the ngspice AC
    ratio pin42/pin41; the stochastic source itself is behavioral and only
    sanity-checked for level
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, build_driver, render
from tests.test_mg1_noise_spice import (
    OUTDIV,
    SQDIV,
    VTH,
    f_hand,
    lfo_tran,
    measured_freq,
    noise_ac,
)

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)


@pytest.fixture(scope="session")
def mg1_bin() -> Path:
    return build_driver(REPO / "dsp" / "mg1_noise.dsp", "mg1_noise_ir")


def dsp_freq(x: np.ndarray) -> float:
    t = np.arange(len(x)) / FS
    x = x - np.mean(x)
    idx = np.where((x[:-1] < 0) & (x[1:] >= 0))[0]
    tc = t[idx] + (-x[idx]) / (x[idx + 1] - x[idx]) / FS
    assert len(tc) >= 3
    return 1.0 / np.mean(np.diff(tc))


# 3%: both sides implement the same rate law; residuals are the SPICE deck's
# comparator transition softness and Early-term leftovers (sub-1%) plus the
# crossing-interpolation resolution of a few captured cycles
LFO_FREQ_RTOL = 0.03


@pytest.mark.parametrize("vfc1", [0.0, 1.0])
def test_lfo_frequency_matches_spice(mg1_bin, vfc1):
    spice_f = measured_freq(lfo_tran(vfc1)["t"], lfo_tran(vfc1)["tri"])
    n = int(FS * max(8.0 / f_hand(vfc1=vfc1), 1.0))
    tri = render(mg1_bin, "outsel=0", f"vfc1={vfc1}", n=n)
    assert dsp_freq(tri) == pytest.approx(spice_f, rel=LFO_FREQ_RTOL)


def test_lfo_waveforms_match_spice(mg1_bin):
    """Levels and shape metrics of all four LFO outputs at the default rate.
    Triangle level anchors at the panel's 5 Vpp print (pin 34 +/-2.5 V,
    RHYS 35.75k panel-anchored - see the netlist header)."""
    d = lfo_tran(0.0)
    n = int(FS * 8.0 / f_hand())
    tri = render(mg1_bin, "outsel=0", n=n)
    inv = render(mg1_bin, "outsel=1", n=n)
    sq = render(mg1_bin, "outsel=2", n=n)
    sin_ = render(mg1_bin, "outsel=3", n=n)

    # triangle: amplitude vs SPICE, and it is the inverted pin-35 signal
    assert tri.max() == pytest.approx(d["p34"].max(), rel=0.02)
    assert tri.min() == pytest.approx(d["p34"].min(), rel=0.02)
    assert np.allclose(inv, -tri, atol=1e-9)
    assert tri.max() == pytest.approx(OUTDIV * VTH, rel=0.02)

    # square: levels and duty
    assert sq.max() == pytest.approx(d["p36"].max(), rel=0.02)
    assert sq.min() == pytest.approx(d["p36"].min(), rel=0.02)
    assert sq.max() == pytest.approx(SQDIV * 13.0, rel=0.02)
    assert abs(np.mean(sq > 0) - np.mean(d["p36"] > 0)) < 0.03

    # sine (as-read shaper): 2x rate, extremes within the piecewise-diode
    # approximation of the true exponential knee (5%)
    assert dsp_freq(sin_) == pytest.approx(2 * measured_freq(d["t"], d["tri"]), rel=LFO_FREQ_RTOL)
    assert sin_.max() == pytest.approx(d["p37"].max(), rel=0.05)
    assert sin_.min() == pytest.approx(d["p37"].min(), rel=0.05)


# pink filter magnitude tolerance over the full 20 Hz..20 kHz referee band:
# the matched-z shelves + anchor-fitted lone pole realization keeps the
# digital-vs-analog residual under 0.3 dB at 44.1/48/96k
PINK_TOL_DB = 0.5


def test_pink_filter_matches_spice_ac(mg1_bin):
    ir = render(mg1_bin, "outsel=6")
    h = np.fft.rfft(ir)
    fr = np.fft.rfftfreq(len(ir), d=1.0 / FS)
    dsp_db = 20 * np.log10(np.maximum(np.abs(h), 1e-12))

    ac = noise_ac(0.5)
    spice_db = ac["p42"] - ac["p41"]
    mask = (ac["f"] >= 20.0) & (ac["f"] <= 20e3)
    f = ac["f"][mask]
    dsp_on_f = np.interp(np.log10(f), np.log10(fr[1:]), dsp_db[1:])
    err = np.abs(dsp_on_f - spice_db[mask])
    assert err.max() < PINK_TOL_DB, (
        f"pink filter off by {err.max():.2f} dB at {f[np.argmax(err)]:.0f} Hz"
    )


def test_noise_levels_sane(mg1_bin):
    """The stochastic source is behavioral (uniform no.noise scaled to the
    2 mV rms the SPICE deck injects); only levels are asserted, the spectrum
    shaping is covered by the filter test above."""
    white = render(mg1_bin, "outsel=4")
    pink = render(mg1_bin, "outsel=5")
    # 2 mV * 1/sqrt(3) (uniform rms) * midband white gain 444.7 at ng=0.5
    # (full-res-confirmed chain: (1 + 1M/28.2k) * 1M/82k)
    assert np.std(white) == pytest.approx(2e-3 / np.sqrt(3) * 444.65, rel=0.05)
    # noise_gain trims the white level per the first-stage gain law
    loud = render(mg1_bin, "outsel=4", "noise_gain=1.0")
    assert np.std(loud) / np.std(white) == pytest.approx(2606.9 / 444.65, rel=0.05)
    # pinking boosts the power-weighted band average (~2.5x for this ladder)
    assert 1.5 < np.std(pink) / np.std(white) < 4.0
