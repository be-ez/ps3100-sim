"""Compare the Faust KBD-Trigger model (dsp/trigger.dsp) against the SPICE
referee (netlists/klm76-trigger.cir).

Both are driven with the SAME key schedule (the netlist's behavioural keyboard
via its k*/Vstep params, the Faust model via the matching sliders rendered
offline with tests/impulse_driver.cpp) and the three conditioned outputs are
compared: the idle-high / asserted-low levels, and the low-region (trigger)
edges - the headline single-vs-multiple legato behaviour. The DSP reproduces
the analog bus DC levels from the R202/R205/Rkbd divider and the AC-coupling
time constant, so the comparison is on trigger EDGES and LEVELS within the
tolerances below (comparator lag + bus edge-shape differences).
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, REPO, build_driver, render
from tests.test_trigger_spice import low_regions, out_high_level, run_trig, vkbd_for_bus

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

EDGE_ABS = 8e-3  # trigger-edge agreement: comparator lag + bus edge shape + print step
WIDTH_ABS = 10e-3  # MULTIPLE pulse-width agreement
LEVEL_TOL = 0.2  # idle-high / asserted-low level agreement, volts

SEL = {"trigout": 0, "single": 1, "mult": 2}


@pytest.fixture(scope="session")
def trig_bin() -> Path:
    return build_driver(REPO / "dsp" / "trigger.dsp", "trigger_ir")


def render_dsp(trig_bin, which, schedule, possel=3, vstep=10.0, tstop=0.55):
    n = int(tstop * FS)
    k1on, k1off, k2on, k2off, k3on, k3off = schedule
    out = render(
        trig_bin,
        f"selout={SEL[which]}",
        f"possel={possel}",
        f"Vstep={vstep}",
        f"k1on={k1on}",
        f"k1off={k1off}",
        f"k2on={k2on}",
        f"k2off={k2off}",
        f"k3on={k3on}",
        f"k3off={k3off}",
        n=n,
    )
    return {"t": np.arange(n) / FS, which: out}


LEGATO = (0.05, 0.45, 0.15, 0.30, 1e9, 1e9)
THREE = (0.05, 0.60, 0.20, 0.60, 0.35, 0.60)


def _match_regions(sp_reg, dp_reg, edge_abs=EDGE_ABS, width_abs=WIDTH_ABS):
    assert len(sp_reg) == len(dp_reg), f"region count spice {sp_reg} vs dsp {dp_reg}"
    for (sa, sb), (da, db) in zip(sp_reg, dp_reg):
        assert da == pytest.approx(sa, abs=edge_abs), f"onset dsp {da:.4f} vs spice {sa:.4f}"
        assert (db - da) == pytest.approx(sb - sa, abs=width_abs), (
            f"width dsp {(db - da) * 1e3:.1f} ms vs spice {(sb - sa) * 1e3:.1f} ms"
        )


def test_idle_and_asserted_levels_match(trig_bin):
    """All three jacks idle at the same active-low HIGH (~+4.1 V) and pull to
    ~0 V when asserted, in both simulators."""
    sp = run_trig(schedule=LEGATO)
    hi = out_high_level()
    idle_sp = sp["t"] < 0.04
    for which in ("trigout", "single", "mult"):
        dp = render_dsp(trig_bin, which, LEGATO)
        idle_dp = dp["t"] < 0.04
        assert sp[which][idle_sp].mean() == pytest.approx(hi, abs=LEVEL_TOL), f"spice {which} idle"
        assert dp[which][idle_dp].mean() == pytest.approx(hi, abs=LEVEL_TOL), f"dsp {which} idle"
        assert dp[which].min() < 0.1, f"dsp {which} asserted-low"


def test_single_level_matches_spice(trig_bin):
    """SINGLE holds one continuous assertion across the legato phrase in both."""
    sp = run_trig(schedule=LEGATO)
    dp = render_dsp(trig_bin, "single", LEGATO)
    sp_reg = low_regions(sp["t"], sp["single"])
    dp_reg = low_regions(dp["t"], dp["single"])
    assert len(sp_reg) == 1 and len(dp_reg) == 1
    _match_regions(sp_reg, dp_reg, width_abs=15e-3)
    # levels
    assert dp["single"][dp["t"] < 0.04].mean() == pytest.approx(
        sp["single"][sp["t"] < 0.04].mean(), abs=LEVEL_TOL
    )
    assert dp["single"].min() < 0.1


def test_trigout_level_matches_spice(trig_bin):
    """Internal TRIG OUT (SELECT pos 3) is a held level in both simulators."""
    sp = run_trig(schedule=LEGATO, possel=3)
    dp = render_dsp(trig_bin, "trigout", LEGATO, possel=3)
    _match_regions(
        low_regions(sp["t"], sp["trigout"]), low_regions(dp["t"], dp["trigout"]), width_abs=15e-3
    )


def test_multiple_pulses_match_spice(trig_bin):
    """MULTIPLE emits one pulse per key-down in both; onsets and widths agree."""
    sp = run_trig(schedule=LEGATO)
    dp = render_dsp(trig_bin, "mult", LEGATO)
    sp_reg = low_regions(sp["t"], sp["mult"])
    dp_reg = low_regions(dp["t"], dp["mult"])
    assert len(sp_reg) == 2, f"spice pulses {sp_reg}"
    _match_regions(sp_reg, dp_reg)


def test_three_note_multiple_and_single(trig_bin):
    """Three overlapping keys -> three MULTIPLE pulses, one held SINGLE, in
    both simulators (the retrigger-per-attack vs one-per-phrase contrast)."""
    sp = run_trig(schedule=THREE, tstop=0.70)
    dp_m = render_dsp(trig_bin, "mult", THREE, tstop=0.70)
    dp_s = render_dsp(trig_bin, "single", THREE, tstop=0.70)
    sp_m = low_regions(sp["t"], sp["mult"])
    sp_s = low_regions(sp["t"], sp["single"])
    assert len(sp_m) == 3
    _match_regions(sp_m, low_regions(dp_m["t"], dp_m["mult"]))
    _match_regions(sp_s, low_regions(dp_s["t"], dp_s["single"]), width_abs=15e-3)


def test_select_sensitivity_matches_spice(trig_bin):
    """Bus parked at ~0.45 V: internal TRIG OUT triggers at SELECT pos 3, not
    pos 4 - reproduced by the DSP tap thresholds."""
    vk = vkbd_for_bus(0.45)
    sched = (0.05, 0.50, 1e9, 1e9, 1e9, 1e9)
    for pos, should in [(3, True), (4, False)]:
        dp = render_dsp(trig_bin, "trigout", sched, possel=pos, vstep=vk, tstop=0.30)
        triggered = dp["trigout"].min() < 0.1
        assert triggered == should, f"pos {pos}: DSP triggered={triggered}, expected {should}"
