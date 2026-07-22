"""Validate the KLM-62D RELEASE CONTROL netlist (netlists/klm62d-relctl.cir)
against hand theory.

The board is a static three-level source with one slewed transition path:
pin 5 ("TO GATE RELEASE TERMINAL") sits at

  - +11.610 V (= 14.9*18k/23.1k, both switches' transistors off) when the
    panel RELEASE switch grounds pin 6,
  - ~+5.8..+8.0 V (VR1 HALF D ADJ trim) when only the HD switch grounds
    pin 4 (Q2 saturated through R13 + VR1),
  - ~+0.14 V (Q1 saturated) when both switch lines are open (idle).

C1 (1 uF to the stiff +14.9 V rail = an AC ground at the HD node) slews
every state change of Q1 - entering or leaving the fully-damped state takes
~40..60 ms - while the Q2 (half-damp) path has no capacitor and switches in
microseconds.

Hand theory here is a piecewise-linear nodal model of the transcription
(ideal-diode BJT bases at VBE, saturation as a collector-current ceiling
BETA*ib, C1 integrated explicitly). It reproduces every SPICE level to
<0.05 V and every transition time to <7 % - the same model is ported to
dsp/relctl.dsp, where tests/test_relctl_dsp.py referees it against SPICE
directly.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm62d-relctl.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# --- netlist constants ---------------------------------------------------
VCC = 14.9
R7 = R8 = R9 = R10 = R11 = R12 = 100e3
R13 = 3.9e3
RVR1 = 4.7e3
R14 = 5.1e3
R15 = 18e3
C1 = 1e-6

# --- hand-theory constants (physical estimates, not SPICE fits) ----------
# VBE: VT*ln(Ic/IS) for the netlist's IS=10f device is 0.64..0.68 V over the
# 0.5..3 mA collector currents seen here; the base-conduction corner of the
# 100k/100k dividers is what matters and 0.65 V represents it.
VBE = 0.65
BETA = 300.0  # house nominal 2SC945 mid-spread
# Saturation offsets: Vce_sat of a BF=300/BR=1 Gummel-Poon device at forced
# beta ~130 (Q1: 2.9 mA against ~22 uA base) is ~0.14 V; Q2 runs at lower
# forced beta -> ~0.09 V. Order-of-magnitude estimates; level tolerances
# below carry them.
VSAT1 = 0.143
VSAT2 = 0.095

V_FULL = VCC * R15 / (R14 + R15)  # 11.6104 V, transistor-free divider


def run_relctl(
    rel0: int,
    rel1: int,
    trel: float,
    hd0: int,
    hd1: int,
    thd: float,
    halfd: float,
    tstop: float,
    tstep: float | None = None,
    ngspice: str = "ngspice",
) -> dict[str, np.ndarray]:
    """One transient of the release-control deck with substituted switch
    schedule (state 0 before t, state 1 after) and VR1 position."""
    if tstep is None:
        tstep = tstop / 4000
    deck = NETLIST.read_text()
    deck = re.sub(
        r"^\.param rel0.*$",
        f".param rel0={rel0} rel1={rel1} trel={trel} hd0={hd0} hd1={hd1} thd={thd} halfd={halfd}",
        deck,
        count=1,
        flags=re.MULTILINE,
    )
    deck = re.sub(r"^tran .*$", f"tran {tstep} {tstop}", deck, count=1, flags=re.MULTILINE)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            [ngspice, "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out_file = tmpdir / "relctl_out.txt"
        if not out_file.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        data = np.loadtxt(out_file)
    return {"t": data[:, 0], "out": data[:, 1], "hd": data[:, 3], "rel": data[:, 5]}


def cross(t: np.ndarray, v: np.ndarray, level: float, rising: bool = True) -> float:
    """First interpolated crossing time of `level`, nan if none."""
    if rising:
        idx = np.nonzero((v[1:] >= level) & (v[:-1] < level))[0]
    else:
        idx = np.nonzero((v[1:] <= level) & (v[:-1] > level))[0]
    if len(idx) == 0:
        return float("nan")
    i = idx[0]
    return float(t[i] + (t[i + 1] - t[i]) * (level - v[i]) / (v[i + 1] - v[i]))


# --- hand theory: piecewise-linear nodal model ---------------------------
def node_a(rel: bool, b: float) -> float:
    """RELEASE node. Open: R7 pull-up loaded by R9 (to the HD node) and R8
    into Q2's conducting base (stiff at VBE - the node sits >5 V in every
    open-switch state, far above the 2*VBE conduction corner). Grounded:
    the panel switch shorts it."""
    if rel:
        return 0.0
    return (VCC / R7 + b / R9 + VBE / R8) / (1 / R7 + 1 / R9 + 1 / R8)


def db_dt(rel: bool, hd: bool, b: float) -> float:
    """C1 charge balance at the HD node (hd switch open). In via R9 from
    the RELEASE node; out via R10 into the R11/base divider - below the
    2*VBE corner the base is off and the branch is R10+R11, above it the
    base pins its node at VBE."""
    i_in = (node_a(rel, b) - b) / R9
    i_out = b / (R10 + R11) if b <= 2 * VBE else (b - VBE) / R10
    return (i_in - i_out) / C1


def output(rel: bool, b: float, halfd: float) -> float:
    """Output node: R14/R15 divider, Q2's saturated R13+VR1 branch whenever
    RELEASE is open (its base overdrive is ~40x the collector demand), and
    Q1 as a BETA*ib1 current sink clipped at its saturation floor."""
    ib1 = max(0.0, (b - VBE) / R10 - VBE / R11)
    q2on = not rel
    rq2 = R13 + RVR1 * halfd
    g = 1 / R14 + 1 / R15 + (1 / rq2 if q2on else 0.0)
    v_nl = (VCC / R14 + (VSAT2 / rq2 if q2on else 0.0)) / g
    return max(VSAT1, v_nl - BETA * ib1 / g)


def theory_sim(
    rel0: int, hd0: int, rel1: int, hd1: int, halfd: float, tsw: float, tstop: float
) -> tuple[np.ndarray, np.ndarray]:
    """Forward-Euler integration of C1 with the switch schedule; initial B
    is the settled state-0 value (matching SPICE's t=0 DC operating point)."""
    dt = 2e-5
    b = 0.0
    if not hd0:
        for _ in range(int(1.0 / dt)):  # settle 1 s >> 5 tau
            b += db_dt(bool(rel0), False, b) * dt
    n = int(tstop / dt)
    t = np.arange(n) * dt
    out = np.empty(n)
    for i in range(n):
        rel, hd = (bool(rel0), bool(hd0)) if t[i] < tsw else (bool(rel1), bool(hd1))
        out[i] = output(rel, b, halfd)
        b = 0.0 if hd else b + db_dt(rel, hd, b) * dt
    return t, out


def v_half_ideal(halfd: float) -> float:
    """Half-damp level with an ideal (0 V Vce) saturated Q2."""
    rq2 = R13 + RVR1 * halfd
    g = 1 / R14 + 1 / R15 + 1 / rq2
    return (VCC / R14) / g


# transition-time tolerance: the hand model's residuals vs SPICE are <7 %
# (junction softness around the conduction corner and the finite base-node
# stiffness are not in the piecewise-linear theory); 20 % + the print step
# leaves physical headroom without letting an RC-scale error through.
TIME_RTOL = 0.20
TIME_ABS = 2e-3
TSW = 0.2


def test_full_release_level():
    """RELEASE grounded: both transistors off - the exact R14/R15 divider,
    regardless of the HD switch (RELEASE dominates)."""
    for hd in (0, 1):
        r = run_relctl(1, 1, 1.0, hd, hd, 1.0, 1.0, tstop=0.3)
        assert r["out"][-1] == pytest.approx(V_FULL, rel=1e-3), f"hd={hd}"


@pytest.mark.parametrize("halfd", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_half_damp_level_tracks_vr1(halfd):
    """HD grounded, RELEASE open: Q2 saturated through R13 + VR1*halfd.
    SPICE must sit just ABOVE the ideal-saturation divider (Vce2 > 0) and
    within the ~0.1 V saturation offset plus margin."""
    r = run_relctl(0, 0, 1.0, 1, 1, 1.0, halfd, tstop=0.3)
    v = r["out"][-1]
    ideal = v_half_ideal(halfd)
    assert v > ideal, f"halfd={halfd}: {v:.3f} below ideal-sat {ideal:.3f}"
    assert v == pytest.approx(ideal, abs=0.25), f"halfd={halfd}"


def test_half_damp_level_monotone_in_vr1():
    levels = [run_relctl(0, 0, 1.0, 1, 1, 1.0, k, tstop=0.3)["out"][-1] for k in (0.0, 0.5, 1.0)]
    assert levels[0] < levels[1] < levels[2]
    # trim span endpoints (ideal-sat + saturation offset headroom)
    assert levels[0] == pytest.approx(5.75, abs=0.3)
    assert levels[2] == pytest.approx(7.94, abs=0.3)


def test_idle_damped_state():
    """Both switches open: Q1 saturated (forced beta ~130 << BF) - the
    terminal is a diode-drop-scale floor - and the internal divider nodes
    match the hand solve (RELEASE ~6.35 V, HD ~3.50 V with VBE=0.65)."""
    r = run_relctl(0, 0, 1.0, 0, 0, 1.0, 1.0, tstop=0.4)
    assert r["out"][-1] < 0.25
    # hand solve: A = (VCC + VBE + B)/3, B = (A + VBE)/2
    a_th = (2 * VCC + 3 * VBE) / 5
    b_th = (a_th + VBE) / 2
    assert r["rel"][-1] == pytest.approx(a_th, abs=0.15)
    assert r["hd"][-1] == pytest.approx(b_th, abs=0.15)


def test_hd_engage_is_fast():
    """Closing HD shorts C1's node through the switch (tau ~ Ron*C1 = 2 us):
    the half-damp level appears with NO audible lag."""
    r = run_relctl(0, 0, 1.0, 0, 1, TSW, 1.0, tstop=TSW + 0.1, tstep=2e-5)
    t, v = r["t"], r["out"]
    pre = v[t < TSW][-1]
    post = v[-1]
    assert pre < 0.25 and post == pytest.approx(7.97, abs=0.3)
    assert cross(t, v, 0.9 * post) - TSW < 2e-3


def test_hd_disengage_slew():
    """Opening HD: C1 must recharge through the 100k lattice before Q1
    saturates - the fall from half-damp to fully damped is slewed. Theory
    integrates the same RC; ~45 ms to the midpoint."""
    tstop = TSW + 0.4
    r = run_relctl(0, 0, 1.0, 1, 0, TSW, 1.0, tstop=tstop)
    tt, vt = theory_sim(0, 1, 0, 0, 1.0, TSW, tstop)
    mid = 0.5 * (r["out"][r["t"] < TSW][-1] + r["out"][-1])
    t_sp = cross(r["t"], r["out"], mid, rising=False) - TSW
    t_th = cross(tt, vt, mid, rising=False) - TSW
    assert r["out"][-1] < 0.25
    assert abs(t_sp - t_th) < TIME_RTOL * t_th + TIME_ABS, f"spice {t_sp:.4f} vs theory {t_th:.4f}"


def test_release_engage_delay():
    """Closing RELEASE from idle: Q2's base is cut instantly but the
    terminal can only rise once C1 discharges Q1's base drive below
    saturation - a ~40 ms declick delay, then the full 11.6 V."""
    tstop = TSW + 0.4
    r = run_relctl(0, 1, TSW, 0, 0, 1.0, 1.0, tstop=tstop)
    tt, vt = theory_sim(0, 0, 1, 0, 1.0, TSW, tstop)
    assert r["out"][r["t"] < TSW][-1] < 0.25
    assert r["out"][-1] == pytest.approx(V_FULL, rel=1e-3)
    mid = 0.5 * (V_FULL + VSAT1)
    t_sp = cross(r["t"], r["out"], mid) - TSW
    t_th = cross(tt, vt, mid) - TSW
    assert abs(t_sp - t_th) < TIME_RTOL * t_th + TIME_ABS, f"spice {t_sp:.4f} vs theory {t_th:.4f}"


def test_release_disengage_two_stage():
    """Opening RELEASE: Q2 saturates within microseconds (no cap on its
    path) - the terminal steps down to the half-damp level - then Q1's
    slewed turn-on takes it the rest of the way to the damped floor."""
    tstop = TSW + 0.4
    r = run_relctl(1, 0, TSW, 0, 0, 1.0, 1.0, tstop=tstop, tstep=5e-5)
    t, v = r["t"], r["out"]
    assert v[t < TSW][-1] == pytest.approx(V_FULL, rel=1e-3)
    # fast stage: below 9 V (past the 7.97 V half level + margin) in < 1 ms
    assert cross(t, v, 9.0, rising=False) - TSW < 1e-3
    # slow stage: the damped floor arrives on the C1 timescale, per theory
    tt, vt = theory_sim(1, 0, 0, 0, 1.0, TSW, tstop)
    t_sp = cross(t, v, 1.0, rising=False) - TSW
    t_th = cross(tt, vt, 1.0, rising=False) - TSW
    assert v[-1] < 0.25
    assert abs(t_sp - t_th) < TIME_RTOL * t_th + TIME_ABS, f"spice {t_sp:.4f} vs theory {t_th:.4f}"
