"""Validate the KLM-63 WAVE FORM CONTROL netlist against hand theory
(plan-style SPICE-vs-theory gate for this board):

  - each panel selection puts the transcribed divider level on the right
    rail: WFR (pin 12) carries the saw clamp / TRI ADJ level, WFD (pin 13)
    carries the pulse-width ladder, exactly as re-read at full resolution
   
  - the D103-clamped ladder-return rail makes every level a stiff divider:
    hand theory below solves the same diode law the netlist models use
    (DSS: IS=2.5n N=1.7; zener: BV=5.1 IBV=1m), so the match is tight
  - the PWM path (pin 16 + PWM IN) follows the zener-offset law with the
    documented ~0.53 V/V sensitivity and the IC11b/Q102 drive ceiling
  - the rails are fast-attack (divider Thevenin * 0.047u), slow-release
    (1M * 0.047u = 47 ms) - the RC that band-limits instrument-wide PWM

The panel-drive assumption these tests inherit (selected pin energized
with +14.9V, saw pin grounded) is argued in the netlist header.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm63-wavectl.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# --- hand theory (same device laws as the netlist model cards) ---
VT = 0.025865  # kT/q at ngspice's 27 C default
VCC = 14.9
ND, ISD = 1.7, 2.5e-9  # DSS (1S1588-class)
VSWING = 13.5  # behavioral 1458 swing limit
RLEAK = 1e6  # R113 / R117


def vdss(i: float) -> float:
    return ND * VT * np.log1p(max(i, 0.0) / ISD)


def ladder_clamp(rtotal: float) -> tuple[float, float]:
    """D103 clamp voltage and chain current for an energized branch of
    total series resistance rtotal ending on the clamp rail."""
    vd = 0.6
    for _ in range(30):
        i = (VCC - vd) / rtotal
        vd = vdss(i)
    return vd, i


def orred(vsrc: float, rth: float) -> float:
    """Level after a steering diode into a 1M rail: the uA-scale load
    current sets both the diode drop and the Thevenin droop."""
    il = 5e-6
    v = 0.0
    for _ in range(50):
        v = vsrc - il * rth - vdss(il)
        il = max(v, 0.0) / RLEAK
    return v


def wfd_pulse(rt: float, rb: float) -> float:
    """Fixed pulse width: +14.9 -> rt -> junction -> rb -> D103 rail."""
    vd, i = ladder_clamp(rt + rb)
    return orred(vd + i * rb, rt * rb / (rt + rb))


def wfr_tri(t: float) -> float:
    """TRI ADJ wiper (t = 1 -> top) through D102, 1458 swing cap."""
    vd, i = ladder_clamp(470 + 4700 + 7500)
    vw = vd + i * (7500 + 4700 * t)
    rup, rlow = 470 + 4700 * (1 - t), 7500 + 4700 * t
    return min(orred(vw, rup * rlow / (rup + rlow)), VSWING)


def wfd_leak_tri() -> float:
    """Triangle mode: the clamp rail leaks onto node Y through the three
    idle dividers' bottom legs (three parallel diodes, sub-uA)."""
    lad, _ = ladder_clamp(470 + 4700 + 7500)
    il = 0.5e-6
    v = 0.0
    for _ in range(50):
        v = lad - ND * VT * np.log1p(il / (3 * ISD))
        il = max(v, 0.0) / RLEAK
    return v


def wfr_leak_pulse(rt: float, rb: float, t: float = 0.5) -> float:
    """Pulse modes: the clamp rail reaches node X through the idle
    triangle chain (R103 + lower pot half) and D102."""
    lad, _ = ladder_clamp(rt + rb)
    return orred(lad, 7500 + 4700 * t)


def wfd_ceiling() -> float:
    """IC11b swing limit through Q102's Vbe (IS=10f) at the R115 current."""
    c = 12.8
    for _ in range(30):
        c = VSWING - VT * np.log((c / 10e3) / 10e-15)
    return c


def wfd_pin16(p: float | None = None) -> float:
    """Pin 16 selected: J = node fed by R110 39k, discharged by the
    D108+zener(5.1)+R116 chain into the PWM node (driven at p volts, or
    floating on R112 150k to -14.9V) and by the D107 load. Bisect KCL."""

    def chain_current(j: float) -> float:
        iz = 50e-6
        for _ in range(80):
            drop = vdss(iz) + 5.1 + VT * np.log(iz / 1e-3)
            rest = (j - drop + VCC) / 183e3 if p is None else (j - drop - p) / 33e3
            if rest <= 0:
                return 0.0
            iz = 0.5 * iz + 0.5 * rest
        return iz

    def load_current(j: float) -> float:
        return max(orred(j, 0.0), 0.0) / RLEAK

    lo, hi = 0.0, VCC
    for _ in range(60):
        j = 0.5 * (lo + hi)
        if (VCC - j) / 39e3 - chain_current(j) - load_current(j) > 0:
            lo = j
        else:
            hi = j
    return min(orred(j, 0.0), wfd_ceiling())


# --- deck runner ---
DEFAULTS = dict(
    sel_tri=0,
    sel_saw=0,
    sel_w=0,
    sel_m=0,
    sel_n=0,
    sel_x=0,
    tri_adj=0.5,
    vpwm=0,
    pwm_on=0,
    dyn=0,
    dynp=0,
    rl_wfr=1e9,
    rl_wfd=1e9,
)


def run_wavectl(params: dict, control: str, outfile: str) -> np.ndarray:
    base = dict(DEFAULTS)
    base.update(params)
    pline = ".param " + " ".join(f"{k}={v:g}" for k, v in base.items())
    deck = NETLIST.read_text()
    deck = re.sub(r"^\.param .*\n\+ .*$", pline, deck, count=1, flags=re.MULTILINE)
    deck = re.sub(r"(?s)\.control.*?\.endc", control, deck, count=1)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            ["ngspice", "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out = tmpdir / outfile
        if not out.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        return np.loadtxt(out)


OP_CONTROL = """.control
op
wrdata wavectl_op.txt v(wfr) v(wfd)
.endc"""

_op_cache: dict[tuple, tuple[float, float]] = {}


def op_point(**params) -> tuple[float, float]:
    """(WFR, WFD) at DC for a selection; cached for reuse by the DSP test."""
    key = tuple(sorted(params.items()))
    if key not in _op_cache:
        d = run_wavectl(params, OP_CONTROL, "wavectl_op.txt")
        _op_cache[key] = (float(d[1]), float(d[3]))
    return _op_cache[key]


# --- tests ---
# 30 mV: hand theory and SPICE share the device laws; the residual is the
# fixed-point truncation and the neglected mA-scale dynamic resistance of
# D103 inside the divider Thevenin
TOL_V = 0.03
# 60 mV for the sub-uA leak levels: at nA..uA the log-law fixed point is
# more sensitive to the neglected series resistances
TOL_LEAK_V = 0.06


@pytest.mark.parametrize("t", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_triangle_levels(t):
    wfr, wfd = op_point(sel_tri=1, tri_adj=t)
    assert wfr == pytest.approx(wfr_tri(t), abs=TOL_V), f"tri_adj={t}"
    assert wfd == pytest.approx(wfd_leak_tri(), abs=TOL_LEAK_V)


def test_triangle_top_hits_swing_limit():
    """The wiper-at-top level (14.36V - D102) exceeds what the 1458 can
    swing on +-14.9V rails: the follower caps the triangle WFR at +13.5V."""
    wfr, _ = op_point(sel_tri=1, tri_adj=1.0)
    assert wfr == pytest.approx(VSWING, abs=0.01)


def test_saw_clamp():
    """Saw grounds pin 11: Q101 saturates onto the WFR output net; the
    level is the rail minus a deep-saturation Vec (tens of mV)."""
    wfr, wfd = op_point(sel_saw=1)
    assert 14.7 < wfr < 14.9
    assert abs(wfd) < 0.01  # ladder rail is not lifted in saw


PULSES = {
    "wide": (dict(sel_w=1), 5.6e3, 5.6e3),
    "mid": (dict(sel_m=1), 3.9e3, 6.2e3),
    "narrow": (dict(sel_n=1), 1.8e3, 6.8e3),
}


@pytest.mark.parametrize("name", list(PULSES))
def test_pulse_width_levels(name):
    sel, rt, rb = PULSES[name]
    wfr, wfd = op_point(**sel)
    assert wfd == pytest.approx(wfd_pulse(rt, rb), abs=TOL_V), name
    assert wfr == pytest.approx(wfr_leak_pulse(rt, rb), abs=TOL_LEAK_V), name


def test_pulse_width_ordering():
    """Duty control is monotone: wide < mid < narrow < ceiling, with the
    multi/PWM rest level inside the fixed-width range."""
    wide = op_point(sel_w=1)[1]
    mid = op_point(sel_m=1)[1]
    narrow = op_point(sel_n=1)[1]
    multi = op_point(sel_x=1)[1]
    assert wide < mid < narrow < wfd_ceiling()
    assert wide < multi < narrow


def test_multi_floating_level():
    wfr, wfd = op_point(sel_x=1)
    assert wfd == pytest.approx(wfd_pin16(None), abs=TOL_V)
    assert abs(wfr) < 0.05  # triangle chain sits on the un-lifted rail


@pytest.mark.parametrize("p", [-10.0, -5.0, -2.0, 0.0, 2.0, 5.0, 12.0])
def test_pwm_law(p):
    _, wfd = op_point(sel_x=1, pwm_on=1, vpwm=p)
    assert wfd == pytest.approx(wfd_pin16(p), abs=TOL_V), f"vpwm={p}"


def test_pwm_sensitivity_and_ceiling():
    """~0.53 V/V in the linear region (R116+zener chain against R110 and
    the 1M load); high drive runs into the IC11b/Q102 ceiling."""
    lo = op_point(sel_x=1, pwm_on=1, vpwm=-5.0)[1]
    hi = op_point(sel_x=1, pwm_on=1, vpwm=5.0)[1]
    assert 0.50 < (hi - lo) / 10.0 < 0.55
    assert op_point(sel_x=1, pwm_on=1, vpwm=12.0)[1] == pytest.approx(wfd_ceiling(), abs=TOL_V)


def test_output_drive():
    """The rails hold under bus loading (48 shapers: ~2k on WFD via the
    100k ladder feeds; WFR loaded lightly at the JFET sources): emitter
    follower / opamp output keep the droop in the mV range."""
    assert op_point(sel_w=1)[1] - op_point(sel_w=1, rl_wfd=2e3)[1] < 0.005
    assert op_point(sel_saw=1)[0] - op_point(sel_saw=1, rl_wfr=10e3)[0] < 0.005


TRAN_CONTROL = """.control
tran 20u 100m 0 uic
wrdata wavectl_tran.txt v(wfd)
.endc"""

_tran_cache: dict[str, np.ndarray] = {}


def dyn_tran(hook: str) -> tuple[np.ndarray, np.ndarray]:
    """Transient with a drive hook: 'dyn' pulses pin 19 (on at 1 ms, off at
    61 ms), 'dynp' steps the PWM node 0 -> 5V at 1 ms with pin 16 held."""
    if hook not in _tran_cache:
        params = {hook: 1}
        if hook == "dynp":
            params["sel_x"] = 1
        _tran_cache[hook] = run_wavectl(params, TRAN_CONTROL, "wavectl_tran.txt")
    d = _tran_cache[hook]
    return d[:, 0], d[:, 1]


def fitted_tau(t: np.ndarray, y: np.ndarray, yfinal: float) -> float:
    """Exponential time constant from the 90..10% error decay."""
    err = np.abs(y - yfinal)
    m = (err < 0.9 * err[0]) & (err > 0.1 * err[0])
    slope = np.polyfit(t[m], np.log(err[m]), 1)[0]
    return -1.0 / slope


def test_attack_dynamics():
    """Selecting a pulse charges C101 through the divider Thevenin
    (R104||R105 + diode rd ~ 3.15k): 10-90% in ~2.2*RC ~ 330 us."""
    t, wfd = dyn_tran("dyn")
    seg = (t > 1e-3) & (t < 4e-3)
    final = wfd[(t > 50e-3) & (t < 60e-3)].mean()
    assert final == pytest.approx(wfd_pulse(5.6e3, 5.6e3), abs=TOL_V)
    t10 = t[seg][np.argmax(wfd[seg] > 0.1 * final)]
    t90 = t[seg][np.argmax(wfd[seg] > 0.9 * final)]
    # 40%: the diode incremental resistance varies over the charge
    assert (t90 - t10) == pytest.approx(2.2 * 47e-9 * 3.15e3, rel=0.4)


def test_release_dynamics():
    """Deselecting blocks the steering diode: the rail can only discharge
    through R113 1M, tau = 47 ms - the instrument's waveform-change lag."""
    t, wfd = dyn_tran("dyn")
    seg = t > 61.5e-3
    tau = fitted_tau(t[seg], wfd[seg], 0.0)
    assert tau == pytest.approx(47e-3, rel=0.1)


def test_pwm_step_dynamics():
    """PWM drives the rail through the zener chain: tau = C101 * (rd107 +
    R110 || (R116 + rd)) ~ 1 ms - the bandwidth of instrument-wide PWM."""
    t, wfd = dyn_tran("dynp")
    seg = (t > 1e-3) & (t < 8e-3)
    final = wfd[(t > 50e-3) & (t < 60e-3)].mean()
    assert final == pytest.approx(wfd_pin16(5.0), abs=TOL_V)
    tau = fitted_tau(t[seg], wfd[seg], final)
    # 35%: the diode/zener incremental resistances move with the level
    assert tau == pytest.approx(1.0e-3, rel=0.35)
