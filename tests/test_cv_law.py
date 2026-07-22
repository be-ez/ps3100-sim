"""Assert the KLM-62D CV drive law extracted from SPICE (netlists/klm62-cv.cir
via analysis/cv_law.py) matches the RESONATORS 2/2 hardware analysis:
exponential law at ~1.8 V/octave, monotone sweep, per-band offsets ordered by
the FC trim defaults, and per-band peak pots isolated to their own band.

Operating point (interface-map mismatch 2): the RES MOD bus is the panel
PEAK FREQ CV jack, -5..+5 V bipolar; cv in [0,1] maps to vrm1 = 10*(cv-0.5)
and cv=0.5 (0 V bus, nothing patched) is the factory-trim anchor."""

import json
import shutil

import numpy as np
import pytest

from analysis.cv_law import (
    FC_DEFAULTS,
    R_FIT_HI,
    R_FIT_LO,
    VRM_MAX,
    VRM_MIN,
    build_grid,
    build_law,
    cv_to_vrm,
    fit_law,
    run_dc,
)

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# Hardware figure: ~2.69 V per e-fold at the 270k matrix inputs (26 mV per
# e-fold at the base times the 104x divider) = 1.86 V/octave of LED current
# at 27 C. Tolerance covers VT temperature spread, the finite-beta base
# current bend, and pot-loading approximations -- not a loosened physics claim.
VOLTS_PER_OCT = 1.8
VOLTS_PER_OCT_TOL = 0.25

# the exponential-law affine fit residual bound, in octaves. Measured rms is
# 0.001..0.006 oct; 0.05 leaves room without letting a wrong law through.
FIT_RMS_MAX_OCT = 0.05

# FC trim defaults are calibrated for one octave between adjacent bands
BAND_SPACING_OCT = 1.0
BAND_SPACING_TOL = 0.1


@pytest.fixture(scope="module")
def grid():
    return build_grid()


@pytest.fixture(scope="module")
def fit(grid):
    return fit_law(grid)


def test_bus_range_is_panel_bipolar(grid):
    """The sweep grid covers the panel PEAK FREQ CV jack range, -5..+5 V
    bipolar (p0023; MG2, the canonical source, is a +/-2.73 V triangle):
    cv 0..1 maps onto vrm1 = -5..+5 V with cv=0.5 at 0 V."""
    assert (VRM_MIN, VRM_MAX) == (-5.0, 5.0)
    assert cv_to_vrm(0.5) == 0.0
    for res in grid.values():
        assert res["cv"][0] == pytest.approx(0.0, abs=1e-9)
        assert res["cv"][-1] == pytest.approx(1.0, abs=1e-9)


def test_factory_anchor_at_zero_bus(grid):
    """cv=0.5 <=> 0 V bus (nothing patched into the jack) is the factory-trim
    operating point: the FC defaults put Rldr at 47k / 23.5k / 11.75k there
    (the audio netlist's octave-stagger anchors)."""
    res = grid["0.5"]
    j = int(np.argmin(np.abs(res["cv"] - 0.5)))
    assert cv_to_vrm(res["cv"][j]) == pytest.approx(0.0, abs=1e-6)
    for i, target in enumerate((47e3, 23.5e3, 11.75e3)):
        assert res["r"][i][j] == pytest.approx(target, rel=0.005)


def test_led_current_volts_per_octave(fit):
    for b in fit["bands"]:
        assert b["volts_per_octave_current"] == pytest.approx(
            VOLTS_PER_OCT, abs=VOLTS_PER_OCT_TOL
        ), f"band {b['band']}: {b['volts_per_octave_current']:.2f} V/oct"


def test_law_is_exponential(fit):
    """log2(Rldr) affine in cv (so log2(f0) is too), and the master-sweep
    slope separates into (per-volt matrix slope) * (cv-to-volts scale)."""
    for b in fit["bands"]:
        assert b["rms_err_oct"] < FIT_RMS_MAX_OCT, f"band {b['band']}"
    slopes = [b["oct_per_cv"] for b in fit["bands"]]
    assert np.ptp(slopes) < 0.1, f"band slopes diverge: {slopes}"
    assert abs(fit["separability_err_oct_per_cv"]) < 0.05


def test_rldr_monotonic_in_cv(grid):
    """Rldr never increases with cv; strictly falls while unclamped."""
    for res in grid.values():
        for i in range(3):
            r = res["r"][i]
            assert np.all(np.diff(r) <= 0)
            ok = (r > R_FIT_LO) & (r < R_FIT_HI)
            assert np.all(np.diff(r[ok]) < 0)


def test_band_offsets_follow_fc_trims(grid, fit):
    """FC trim defaults descend A > B > C (band A pulled furthest negative),
    so Rldr must descend A > B > C at every unclamped grid point, and the
    calibrated offsets sit one octave apart (f0 stagger low -> high)."""
    assert FC_DEFAULTS[0] > FC_DEFAULTS[1] > FC_DEFAULTS[2]
    for res in grid.values():
        ok = (res["r"] > R_FIT_LO).all(axis=0) & (res["r"] < R_FIT_HI).all(axis=0)
        assert np.all(res["r"][0][ok] > res["r"][1][ok])
        assert np.all(res["r"][1][ok] > res["r"][2][ok])
    b = {x["band"]: x["log2_r_at_cv0"] for x in fit["bands"]}
    assert b["A"] - b["B"] == pytest.approx(BAND_SPACING_OCT, abs=BAND_SPACING_TOL)
    assert b["B"] - b["C"] == pytest.approx(BAND_SPACING_OCT, abs=BAND_SPACING_TOL)


def test_peak_pot_isolated_and_monotonic():
    """Turning up one band's peak pot raises that band's f0 (lowers Rldr)
    and leaves the other bands untouched (the 270k matrix has no cross path)."""
    lo = run_dc(pk=(0.2, 0.5, 0.5))
    hi = run_dc(pk=(0.8, 0.5, 0.5))
    j = int(np.argmin(np.abs(lo["cv"] - 0.5)))
    assert hi["r"][0][j] < 0.5 * lo["r"][0][j]
    for i in (1, 2):
        assert hi["r"][i][j] == pytest.approx(lo["r"][i][j], rel=0.01)


def test_cv_law_json_roundtrip(tmp_path, grid, fit):
    """The written cv_law.json reproduces the in-memory grid and fit."""
    law = build_law()
    out = tmp_path / "cv_law.json"
    out.write_text(json.dumps(law, indent=1))
    loaded = json.loads(out.read_text())
    assert set(loaded) == {"meta", "grid", "fit"}
    # deterministic: an independent build matches the fixture's fit
    for b_new, b_fix in zip(loaded["fit"]["bands"], fit["bands"]):
        assert b_new["oct_per_cv"] == pytest.approx(b_fix["oct_per_cv"], rel=1e-6)
        assert b_new["log2_r_at_cv0"] == pytest.approx(b_fix["log2_r_at_cv0"], rel=1e-6)
    for pstr, res in loaded["grid"].items():
        assert np.allclose(res["rldr"], grid[pstr]["r"])
