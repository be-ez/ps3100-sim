"""Validate the KLM-76 KBD-Trigger netlist (netlists/klm76-trigger.cir) against
hand theory: the panel SELECT threshold ladder, the fixed SINGLE/MULTIPLE
comparator thresholds, the output levels, and - the headline - the
single-vs-multiple legato behaviour.

The netlist is a node-by-node transcription of the "KBD. Trigger" scan
.
The keyboard contacts and the KLM-69 trigger driver are OFF this sheet, so
they are a behavioural voltage source Vkbd (rises one step per held key)
through Rkbd into pin 29; the conditioning network around it is the faithful
transcription.

Circuit facts pinned here:
  - bus idles just above ground (R202 300k pull-up vs R205 1k pull-down); a
    key press drives it POSITIVE across the thresholds.
  - IC21 internal TRIG OUT: bus vs panel SELECT tap. DC -> level.
  - IC22a SINGLE (pin 31): bus vs fixed +98.7 mV (R204 1k / R214 150k to
    +14.9). DC -> a level asserted the whole time any key is held.
  - IC22b MULTIPLE (pin 30): bus AC-coupled (C203 0.068 / R206 270k) vs fixed
    +67.4 mV (R207b 1k / R215 220k to +14.9) -> a pulse on every positive bus
    edge (new key attack).
  - all three jacks: ~+4.6 V idle, pulled to GND to trigger (active low).
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm76-trigger.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# wrdata order in the deck's .control block
TRACES = ["bus", "trigout", "single", "mult", "acn", "wiper", "cmp1"]

# --- hand-theory constants traced to the netlist ---
VCC = 14.9
VP15 = 15.0
SWING = 13.5  # op1458 comparator swing
RO = 300.0  # op1458 output resistance
# panel ladder: +15 - R213 11k - t5 - 4x100 - t1 - R208 100 - GND
LADDER_TOTAL = 11e3 + 5 * 100.0
LADDER_I = VP15 / LADDER_TOTAL  # 1.3043 mA
TAP = {i: LADDER_I * (i * 100.0) for i in range(1, 6)}  # 130.4 .. 652.2 mV
VTH_SINGLE = VCC * 1e3 / (1e3 + 150e3)  # R204/R214 -> 98.68 mV
VTH_MULT = VCC * 1e3 / (1e3 + 220e3)  # R207b/R215 -> 67.42 mV
# active-low output high level: (SWING - Vd) through RO + R216(1.8k) + R218(1k)
R_SER, R_LOAD = 1.8e3, 1e3


def out_high_level(vd: float = 0.68) -> float:
    return (SWING - vd) * R_LOAD / (RO + R_SER + R_LOAD)


def bus_dc(vkbd: float, rkbd: float = 10e3) -> float:
    """Static bus voltage for a steady Vkbd (all timing caps open, D201 off)."""
    g_up, g_dn, g_kbd = 1.0 / 300e3, 1.0 / 1e3, 1.0 / (100.0 + rkbd)
    return (VCC * g_up + vkbd * g_kbd) / (g_up + g_dn + g_kbd)


def vkbd_for_bus(vb: float, rkbd: float = 10e3) -> float:
    """Invert bus_dc: the steady Vkbd that parks the bus at vb."""
    g_up, g_dn, g_kbd = 1.0 / 300e3, 1.0 / 1e3, 1.0 / (100.0 + rkbd)
    return (vb * (g_up + g_dn + g_kbd) - VCC * g_up) / g_kbd


def run_trig(
    possel: int = 3,
    schedule: tuple[float, float, float, float, float, float] = (0.05, 0.45, 0.15, 0.30, 1e9, 1e9),
    vstep: float = 10.0,
    rkbd: float = 10e3,
    tr: float = 1e-4,
    tstop: float = 0.55,
    tstep: float | None = None,
    ngspice: str = "ngspice",
) -> dict[str, np.ndarray]:
    k1on, k1off, k2on, k2off, k3on, k3off = schedule
    if tstep is None:
        tstep = tstop / 5500
    deck = NETLIST.read_text()
    deck = re.sub(
        r"^\.param k1on.*$",
        f".param k1on={k1on} k1off={k1off}  k2on={k2on} k2off={k2off}  k3on={k3on} k3off={k3off}",
        deck,
        count=1,
        flags=re.MULTILINE,
    )
    deck = re.sub(
        r"^\.param Vstep.*$",
        f".param Vstep={vstep} Rkbd={rkbd} tr={tr}",
        deck,
        count=1,
        flags=re.MULTILINE,
    )
    deck = re.sub(
        r"^\.param possel.*$", f".param possel={possel}", deck, count=1, flags=re.MULTILINE
    )
    deck = re.sub(r"^tran .*$", f"tran {tstep} {tstop}", deck, count=1, flags=re.MULTILINE)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            [ngspice, "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out = tmpdir / "trig_out.txt"
        if not out.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        data = np.loadtxt(out)
    r = {"t": data[:, 0]}
    for i, name in enumerate(TRACES):
        r[name] = data[:, 2 * i + 1]
    return r


def low_regions(t: np.ndarray, v: np.ndarray, thr: float = 1.0) -> list[tuple[float, float]]:
    """Intervals where v < thr (jack pulled toward GND = asserted)."""
    lo = v < thr
    d = np.diff(lo.astype(int))
    starts = list(t[1:][d == 1])
    ends = list(t[1:][d == -1])
    if lo[0]:
        starts.insert(0, float(t[0]))
    if lo[-1]:
        ends.append(float(t[-1]))
    return list(zip(starts, ends))


# ---------------------------------------------------------------- levels
def test_output_levels_and_idle_bus():
    r = run_trig()
    idle = r["t"] < 0.04
    assert r["bus"][idle].mean() == pytest.approx(bus_dc(0.0), abs=3e-3)
    hi = out_high_level()
    for name in ["trigout", "single", "mult"]:
        assert r[name][idle].mean() == pytest.approx(hi, abs=0.15), f"{name} idle-high"
        assert r[name].min() < 0.05, f"{name} never pulled to GND"
    # active-low high level is set by the RO+R216+R218 divider, ~4.1 V
    assert 3.9 < hi < 4.4


# ---------------------------------------------------------- SELECT ladder
@pytest.mark.parametrize("pos", [1, 2, 3, 4, 5])
def test_select_tap_voltage_matches_ladder(pos):
    r = run_trig(possel=pos)
    idle = r["t"] < 0.04
    assert r["wiper"][idle].mean() == pytest.approx(TAP[pos], abs=2e-3), f"pos {pos}"


def test_select_off_disables_trigger():
    """OFF ties the SELECT wiper to +15 V (R207p) -> IC21 '+' rails high ->
    internal TRIG OUT stuck at idle-high; the key press never triggers it."""
    r = run_trig(possel=0)
    idle = r["t"] < 0.04
    assert r["wiper"][idle].mean() > 14.0
    assert r["trigout"].min() > 3.5, "OFF must not let pin 33 trigger"


# -------------------------------------------------- headline: single vs mult
def test_single_holds_multiple_retriggers_legato():
    """Two overlapping keys (legato): key1 [0.05,0.45], key2 [0.15,0.30].
    SINGLE (and the internal TRIG OUT) assert ONCE, continuously, for the whole
    held span -> no legato retrigger. MULTIPLE emits a SEPARATE pulse at each
    of the two key-downs -> retrigger per attack."""
    r = run_trig()
    t = r["t"]
    sng = low_regions(t, r["single"])
    trg = low_regions(t, r["trigout"])
    mul = low_regions(t, r["mult"])
    # SINGLE: exactly one region covering ~[0.05, 0.45]
    assert len(sng) == 1, f"SINGLE regions {sng}"
    assert sng[0][0] == pytest.approx(0.05, abs=5e-3)
    assert sng[0][1] == pytest.approx(0.45, abs=1e-2)
    # internal TRIG OUT is a level too (SELECT pos 3), same held span
    assert len(trg) == 1 and trg[0][1] - trg[0][0] > 0.35
    # MULTIPLE: two disjoint pulses, one per key-down, each much shorter
    assert len(mul) == 2, f"MULTIPLE regions {mul}"
    assert mul[0][0] == pytest.approx(0.05, abs=5e-3)
    assert mul[1][0] == pytest.approx(0.15, abs=5e-3)
    for a, b in mul:
        assert 0.02 < b - a < 0.08, f"pulse {a:.3f}-{b:.3f} not a short retrigger"


def test_multiple_pulse_per_key_three_note_run():
    """Three staccato-into-legato keys -> three MULTIPLE pulses; SINGLE stays
    asserted across the whole run because the bus never returns to idle."""
    sched = (0.05, 0.60, 0.20, 0.60, 0.35, 0.60)  # three key-downs, all held to 0.60
    r = run_trig(schedule=sched, tstop=0.70)
    t = r["t"]
    mul = low_regions(t, r["mult"])
    sng = low_regions(t, r["single"])
    downs = [0.05, 0.20, 0.35]
    assert len(mul) == 3, f"expected one pulse per key-down, got {mul}"
    for (a, _), d in zip(mul, downs):
        assert a == pytest.approx(d, abs=6e-3)
    assert len(sng) == 1 and sng[0][1] - sng[0][0] > 0.50, "SINGLE must hold across the run"


def test_multiple_does_not_pulse_on_release():
    """A single key held long, then released. MULTIPLE fires once (press) and
    is silent on release; SINGLE holds for the whole hold then de-asserts."""
    r = run_trig(schedule=(0.05, 0.35, 1e9, 1e9, 1e9, 1e9), tstop=0.55)
    t = r["t"]
    mul = low_regions(t, r["mult"])
    sng = low_regions(t, r["single"])
    assert len(mul) == 1, f"release must not pulse MULTIPLE: {mul}"
    assert mul[0][0] == pytest.approx(0.05, abs=5e-3)
    assert len(sng) == 1
    assert sng[0][0] == pytest.approx(0.05, abs=5e-3)
    assert sng[0][1] == pytest.approx(0.35, abs=1e-2)


# -------------------------------------------------- fixed comparator thresholds
@pytest.mark.parametrize("above", [False, True])
def test_single_threshold_trip_point(above):
    """Park the bus just below / just above the fixed +98.7 mV SINGLE
    threshold and confirm the DC comparator does / does not assert."""
    vb = VTH_SINGLE + (0.02 if above else -0.02)
    vk = vkbd_for_bus(vb)
    r = run_trig(schedule=(0.05, 0.50, 1e9, 1e9, 1e9, 1e9), vstep=vk, tstop=0.30)
    held = (r["t"] > 0.15) & (r["t"] < 0.30)
    assert r["bus"][held].mean() == pytest.approx(vb, abs=5e-3)
    if above:
        assert r["single"][held].mean() < 0.1, "should trigger above threshold"
    else:
        assert r["single"][held].mean() > 3.5, "should not trigger below threshold"


def test_multiple_ignores_subthreshold_edges():
    """A key step too small to spike the AC node (acn) above +67.4 mV makes no
    MULTIPLE pulse; a large step does. (Encodes the fixed +67.4 mV threshold.)"""
    # tiny step: bus rise ~ 40 mV -> acn peak << 67 mV -> no pulse
    small = run_trig(
        schedule=(0.05, 0.50, 1e9, 1e9, 1e9, 1e9), vstep=vkbd_for_bus(0.09) * 0.4, tstop=0.30
    )
    big = run_trig(schedule=(0.05, 0.50, 1e9, 1e9, 1e9, 1e9), vstep=10.0, tstop=0.30)
    assert small["mult"].min() > 3.5, "sub-threshold edge must not pulse MULTIPLE"
    assert big["mult"].min() < 0.1, "large edge must pulse MULTIPLE"


def test_multiple_pulse_width_follows_rc_law():
    """MULTIPLE pulse width ~ (R206+Rbus)*C203 * ln(acn_peak / Vth_m), and it
    lengthens with a bigger key step (bigger acn spike)."""
    tau = (270e3 + 900.0) * 0.068e-6  # R206 + bus Thevenin, times C203
    widths = {}
    for vstep in (5.0, 10.0, 20.0):
        r = run_trig(schedule=(0.05, 0.50, 1e9, 1e9, 1e9, 1e9), vstep=vstep, tstop=0.30)
        reg = low_regions(r["t"], r["mult"])
        assert len(reg) == 1
        w = reg[0][1] - reg[0][0]
        widths[vstep] = w
        peak = r["acn"].max()
        w_th = tau * np.log(peak / VTH_MULT)
        assert w == pytest.approx(w_th, rel=0.25), (
            f"vstep={vstep}: {w * 1e3:.1f} ms vs {w_th * 1e3:.1f} ms"
        )
    assert widths[5.0] < widths[10.0] < widths[20.0], "wider step -> longer pulse"


# -------------------------------------------------- SELECT sets sensitivity
def test_select_position_sets_internal_sensitivity():
    """With one key parking the bus at ~0.45 V (between tap3 391 mV and tap4
    522 mV), the internal TRIG OUT fires at SELECT positions 1-3 but not 4-5:
    the panel SELECT is a trigger-sensitivity control on the internal path
    only (SINGLE/MULTIPLE use fixed thresholds)."""
    vk = vkbd_for_bus(0.45)
    sched = (0.05, 0.50, 1e9, 1e9, 1e9, 1e9)
    for pos in (1, 2, 3):
        r = run_trig(possel=pos, schedule=sched, vstep=vk, tstop=0.30)
        assert r["trigout"].min() < 0.1, (
            f"pos {pos} (thr {TAP[pos] * 1e3:.0f} mV) should trigger at 0.45 V"
        )
    for pos in (4, 5):
        r = run_trig(possel=pos, schedule=sched, vstep=vk, tstop=0.30)
        assert r["trigout"].min() > 3.5, (
            f"pos {pos} (thr {TAP[pos] * 1e3:.0f} mV) should NOT trigger at 0.45 V"
        )
    # SINGLE (fixed 98.7 mV) triggers regardless of SELECT position
    r = run_trig(possel=5, schedule=sched, vstep=vk, tstop=0.30)
    assert r["single"].min() < 0.1
