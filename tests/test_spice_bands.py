"""Assert SPICE band centers match the KLM-62 stage theory for every color
variant, and that band centers move monotonically with Rldr."""

import shutil

import numpy as np
import pytest

from analysis.ac_analysis import (
    COLORS,
    RLDR_GRID,
    peak_metrics,
    run_ac,
    stage_f0,
    stage_gain_db,
    stage_q,
    staggered,
)

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# AC sweep is 200 pts/decade; parabolic interpolation gets well under 1%
F0_RTOL = 0.02
# only assert bands whose theoretical center stays inside the AC sweep with
# room for the -3 dB skirts; above this the peak detector hits the sweep edge
F0_MAX = 10_000.0
# stage Q and gain sag below the ideal (finite pad impedance, cross-stage
# loading through the shared input bus costs ~1.7 dB) -- allow it, but bound it
Q_RTOL = 0.25
GAIN_TOL_DB = 2.5


@pytest.mark.parametrize("color", COLORS)
@pytest.mark.parametrize("rldr", [4.7e3, 22e3, 470e3])
def test_band_centers_match_theory(color, rldr):
    cin, cfb = COLORS[color]
    rs = staggered(rldr)
    res = run_ac(cin, cfb, rs)
    for k, r in enumerate(rs):
        expected = stage_f0(cin, cfb, r)
        if expected > F0_MAX:
            continue
        measured = peak_metrics(res["freq"], res[f"o{k + 1}"])["f0"]
        assert measured == pytest.approx(expected, rel=F0_RTOL), (
            f"{color} band {k + 1}: SPICE {measured:.1f} Hz vs theory {expected:.1f} Hz"
        )


def test_q_and_gain_near_constant():
    cin, cfb = COLORS["yellow"]
    q_theory = stage_q(cin, cfb)
    # stage gain at f0 on top of the -31.6 dB pad and the input HPF rolloff
    pad_db = 20 * np.log10(27.0 / 1027.0)
    w0h, qh = 2 * np.pi * 47.66, 0.5 * np.sqrt(68.0 / 150.0)
    # rldr chosen so f0 sits well above the 47.7 Hz input HPF corner; below
    # that the fighting slopes depress the product peak beyond the analytic
    # correction (the 470k/41 Hz case is covered by the DSP-vs-SPICE curves)
    for rldr in [22e3, 100e3]:
        res = run_ac(cin, cfb, staggered(rldr))
        m = peak_metrics(res["freq"], res["o1"])
        w = 2 * np.pi * stage_f0(cin, cfb, rldr)
        hpf_db = 20 * np.log10(abs((1j * w) ** 2 / ((1j * w) ** 2 + 1j * w * w0h / qh + w0h**2)))
        gain_theory = stage_gain_db(cin, cfb) + pad_db + hpf_db
        assert m["q"] == pytest.approx(q_theory, rel=Q_RTOL)
        assert abs(m["peak_db"] - gain_theory) < GAIN_TOL_DB


def test_band_centers_monotonic_in_rldr():
    cin, cfb = COLORS["yellow"]
    f0s = []
    for rldr in RLDR_GRID:
        res = run_ac(cin, cfb, staggered(rldr))
        f0s.append([peak_metrics(res["freq"], res[f"o{k + 1}"])["f0"] for k in range(3)])
    f0s = np.array(f0s)
    # RLDR_GRID ascends; f0 proportional to 1/R -> descending, for every band
    for k in range(3):
        assert np.all(np.diff(f0s[:, k]) < 0), f"band {k + 1} not monotonic: {f0s[:, k]}"


def test_provisional_stagger_is_octaves():
    cin, cfb = COLORS["yellow"]
    res = run_ac(cin, cfb, staggered(22e3))
    f0s = [peak_metrics(res["freq"], res[f"o{k + 1}"])["f0"] for k in range(3)]
    assert f0s[1] / f0s[0] == pytest.approx(2.0, rel=0.02)
    assert f0s[2] / f0s[1] == pytest.approx(2.0, rel=0.02)


def test_blend_pot_selects_wet_dry():
    cin, cfb = COLORS["yellow"]
    wet = run_ac(cin, cfb, staggered(22e3), k=1.0)
    dry = run_ac(cin, cfb, staggered(22e3), k=0.0)
    # full dry: near-flat post-HPF response (the wet leg bleeds ~7% through
    # the 1.5k pot even at k=0, tilting it by ~1 dB), no strong resonance
    mid = (dry["freq"] > 300) & (dry["freq"] < 3000)
    assert np.all(np.abs(dry["out"][mid]) < 1.5)
    # full wet: strong band peaks well above the dry level
    assert wet["out"].max() > 1.5
