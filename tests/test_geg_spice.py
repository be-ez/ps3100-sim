"""Validate the KLM-76 GEG netlist (netlists/klm76-geg.cir) against hand
theory: trapezoid levels and linearity, and segment times (delay/attack/
release) predicted from the netlist's differential-pair steering equations
at several panel-pot settings.

The netlist is a node-by-node transcription from the full-resolution scan
re-read; these tests
pin its behaviour so any later edit that changes the timing laws or levels
is caught.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm76-geg.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

TRACES = ["out2", "env", "nd", "cmp1", "cmp2", "out1b", "out1"]

# --- hand-theory constants, traced to the netlist ---
VT = 0.02585  # kT/q at the simulated 27 C
VBE = 0.66  # pair Vbe at the mA-scale tail currents
SWING = 13.5  # op1458 comparator swing
RO = 300.0  # op1458 output resistance (netlist subckt)
C_TIMING = 1e-6  # C102 = C103 (1 uF/25 V Ta per scan)
VREF_REL = -14.9 * 5.4 / 15.4  # R112/R113 release reference (-5.22 V)
ENV_SUS = 0.6576  # Q104 saturation ceiling (measured, current-independent)
ENV_FLOOR = -5.893  # Q106 saturation floor
T_ATT10 = 0.5e-3  # gate->10% overhead of the fastest attack (comparator lag)
# netlist ADJ trim defaults (VR101/VR102/VR103 wiper voltages)
VTRIM_D, VTRIM_A, VTRIM_R = -7.0, 9.5, -0.1


def run_geg(
    kdel: float,
    katt: float,
    krel: float,
    gon: float,
    goff: float,
    tstop: float,
    tstep: float | None = None,
    ngspice: str = "ngspice",
) -> dict[str, np.ndarray]:
    """One transient of the GEG deck with substituted pot/gate params."""
    if tstep is None:
        tstep = tstop / 6000
    deck = NETLIST.read_text()
    deck = re.sub(
        r"^\.param kdel.*$",
        f".param kdel={kdel} katt={katt} krel={krel} gon={gon} goff={goff}",
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
        out_file = tmpdir / "geg_out.txt"
        if not out_file.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        data = np.loadtxt(out_file)
    result = {"t": data[:, 0]}
    for i, name in enumerate(TRACES):
        result[name] = data[:, 2 * i + 1]
    return result


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


def measure(r: dict[str, np.ndarray], gon: float, goff: float) -> dict[str, float]:
    """Envelope metrics of the OUT2 trace: resting level before the gate,
    sustain (mean just before gate-off), final floor, gate->10% rise time
    (t_on: delay + attack overhead), attack and release 10-90 durations."""
    t, v = r["t"], r["out2"]
    floor0 = float(v[t < gon][-1])
    sus_win = v[(t > goff - (goff - gon) * 0.05) & (t < goff)]
    sustain = float(sus_win.mean())
    floor = float(v[t > t[-1] - (t[-1] - goff) * 0.05].mean())
    span = sustain - floor0
    m = t >= gon
    ta10 = cross(t[m], v[m], floor0 + 0.1 * span)
    ta90 = cross(t[m], v[m], floor0 + 0.9 * span)
    m2 = t >= goff
    spanr = sustain - floor
    tr90 = cross(t[m2], v[m2], floor + 0.9 * spanr, rising=False)
    tr10 = cross(t[m2], v[m2], floor + 0.1 * spanr, rising=False)
    return {
        "floor0": floor0,
        "sustain": sustain,
        "floor": floor,
        "t_on": ta10 - gon,
        "attack": ta90 - ta10,
        "release": tr10 - tr90,
    }


# --- hand theory: differential-pair steering with self-consistent ---
# --- base-current loading (the attenuator Thevenin resistances shift ---
# --- the steering by up to ~1 e-fold at the extremes)                ---
def pair_current(
    va0: float,
    ra: float,
    vb0: float,
    rb: float,
    tail_num: float,
    tail_den: float,
    bf: float,
    pnp: bool,
) -> float:
    """Current delivered to the timing cap by the active transistor of a
    steering pair. va0/ra = active-base Thevenin, vb0/rb = dump-base
    Thevenin; tail current = (tail_num - |Vtail|)/tail_den."""
    va, vb = va0, vb0
    share = 0.5
    itot = 1e-3
    for _ in range(30):
        if pnp:
            te = min(va, vb) + VBE
            itot = max((tail_num - te) / tail_den, 1e-9)
            dv = va - vb  # active base above dump base -> starved
        else:
            te = max(va, vb) - VBE
            itot = max((tail_num - abs(te)) / tail_den, 1e-9)
            dv = vb - va
        share = 1.0 / (1.0 + np.exp(dv / VT))
        sgn = 1.0 if pnp else -1.0  # PNP base current lifts its node, NPN sinks it
        va = va0 + sgn * share * itot / bf * ra
        vb = vb0 + sgn * (1 - share) * itot / bf * rb
    return share * itot


def base_thevenin(k: float, vtrim: float) -> tuple[float, float]:
    """Traced attenuator common to all three engines: pot wiper (10 V full
    scale, output impedance of the 10k pot) through 100k, 2k leg to G1,
    1M trim injection."""
    rw = 10e3 * k * (1 - k)  # pot wiper impedance
    g1, g2, g3 = 1.0 / (100e3 + rw), 1.0 / 2e3, 1.0 / 1e6
    v = (10 * k * g1 + vtrim * g3) / (g1 + g2 + g3)
    return v, 1.0 / (g1 + g2 + g3)


def delay_current(k: float) -> float:
    """Q101/Q102 (PNP): dump base grounded, tail R108 10k from +14.9."""
    va0, ra = base_thevenin(k, VTRIM_D)
    return pair_current(va0, ra, 0.0, 0.0, 14.9, 10e3, 250, pnp=True)


def attack_current(k: float) -> float:
    """Q103/Q104 (PNP): dump base grounded, tail R115 4.7k from the +13.5 V
    comparator swing through its output resistance."""
    va0, ra = base_thevenin(k, VTRIM_A)
    return pair_current(va0, ra, 0.0, 0.0, SWING, 4.7e3 + RO, 250, pnp=True)


def release_current(k: float) -> float:
    """Q105/Q106 (NPN): dump base = buffered -5.22 V reference (stiff),
    active base = R116 2k to the reference, R122 100k from the pot wiper,
    R121 56k to -14.9, R120 1M trim; tail R114 1.5k into the comparator
    low swing. Traced sense: k=1 (wiper at +10 V) is FAST."""
    rw = 10e3 * k * (1 - k)
    g_ref, g_pot, g_neg, g_trim = 1.0 / 2e3, 1.0 / (100e3 + rw), 1.0 / 56e3, 1.0 / 1e6
    gsum = g_ref + g_pot + g_neg + g_trim
    va0 = (VREF_REL * g_ref + 10 * k * g_pot - 14.9 * g_neg + VTRIM_R * g_trim) / gsum
    return pair_current(va0, 1.0 / gsum, VREF_REL, 0.0, SWING, 1.5e3 + RO, 300, pnp=False)


def delay_reset(k: float) -> float:
    """C102 clamp level while cmp1 is low: comparator low rail plus the
    steering current's drop across R104 + Ro plus the D103 forward drop."""
    i = delay_current(k)
    vd = 0.045 * np.log(i / 4.3e-9)  # DSS diode at the steering current
    return -SWING + i * (1e3 + RO) + vd


GON = 0.02
POT_KS = [0.2, 0.45, 0.65, 0.8]

# theory-vs-SPICE tolerances (relative). Residuals not in the hand model:
# Early effect (VAF 80..100 with volts of Vce swing on the active device),
# the current dependence of Vbe/diode drops, and the tanh comparator's soft
# output. Release keeps the widest band: its sink collector rides the whole
# 6.5 V envelope and its steering differential is the small difference of
# three traced bias currents (56k/100k/2k network), so percent-level
# component sensitivities compound (measured residual <= ~25 %, systematic).
# Attack carries a consistent +11 % measured-vs-theory stretch: the 10-90 %
# window catches the onset of Q104's soft saturation corner, which scales
# with the ramp itself (ratio 1.11..1.12 at every k) - physical, not drift.
ATT_RTOL = 0.15
DEL_RTOL = 0.15
REL_RTOL = 0.35

ENV_SPAN = ENV_SUS - ENV_FLOOR


@pytest.mark.parametrize("k", POT_KS)
def test_attack_time_matches_theory(k):
    i_th = attack_current(k)
    goff = GON + 1.4 * C_TIMING * ENV_SPAN / i_th + 0.05
    r = run_geg(0.0, k, 1.0, GON, goff, goff + 0.05)
    m = measure(r, GON, goff)
    t_th = 0.8 * ENV_SPAN * C_TIMING / i_th
    assert m["attack"] == pytest.approx(t_th, rel=ATT_RTOL), f"katt={k}"


@pytest.mark.parametrize("k", POT_KS)
def test_release_time_matches_theory(k):
    i_th = release_current(k)
    goff = GON + 0.15
    r = run_geg(0.0, 0.0, k, GON, goff, goff + 1.4 * C_TIMING * ENV_SPAN / i_th + 0.05)
    m = measure(r, GON, goff)
    t_th = 0.8 * ENV_SPAN * C_TIMING / i_th
    assert m["release"] == pytest.approx(t_th, rel=REL_RTOL), f"krel={k}"


@pytest.mark.parametrize("k", POT_KS)
def test_delay_time_matches_theory(k):
    i_th = delay_current(k)
    span = -delay_reset(k)  # ramp from the reset clamp to the 0 V threshold
    goff = GON + 1.4 * C_TIMING * span / i_th + 0.1
    r = run_geg(k, 0.0, 1.0, GON, goff, goff + 0.05)
    m = measure(r, GON, goff)
    t_th = C_TIMING * span / i_th + T_ATT10
    assert m["t_on"] == pytest.approx(t_th, rel=DEL_RTOL), f"kdel={k}"


def test_trapezoid_levels_and_linearity():
    """Mid-pot cycle: OUT2 floor trimmed to ~0 V (VR104), top ~+5.9 V
    (saturation-limited env span through the zener shifter), LINEAR ramps
    (this is what makes it a trapezoid generator, not an RC ADSR),
    complementary outputs riding the same env node."""
    goff = 0.4
    r = run_geg(0.0, 0.5, 0.7, GON, goff, 0.9)
    m = measure(r, GON, goff)
    assert 5.6 < m["sustain"] < 6.1
    assert -0.1 < m["floor"] < 0.15
    t, v = r["t"], r["out2"]
    span = m["sustain"] - m["floor0"]
    for t0, rising in [(GON, True), (goff, False)]:
        mm = t >= t0
        tt, vv = t[mm], v[mm]
        base = m["floor0"] if rising else m["floor"]
        sel = (vv >= base + 0.15 * span) & (vv <= base + 0.85 * span) & (tt < tt[0] + 0.2)
        p = np.polyfit(tt[sel], vv[sel], 1)
        res = vv[sel] - np.polyval(p, tt[sel])
        r2 = 1.0 - res.var() / vv[sel].var()
        assert r2 > 0.999, f"ramp starting at {t0} not linear (R^2={r2:.5f})"
    # complementary outputs: OUT1 ~ env (rests at -5.8 V), /OUT1 ~ -env
    sus_win = (t > goff - 0.05) & (t < goff)
    out1_sus = r["out1"][sus_win].mean()
    out1b_sus = r["out1b"][sus_win].mean()
    assert out1_sus == pytest.approx(0.986 * ENV_SUS, abs=0.1)
    assert out1b_sus == pytest.approx(-out1_sus, abs=0.05)
    assert r["out1"][t < GON][-1] == pytest.approx(0.986 * ENV_FLOOR, abs=0.15)


def test_gate_off_mid_attack_releases_from_partial_level():
    """Dropping the gate before the ramp tops out must release immediately
    from the partial level (no latch to full scale)."""
    # gate must outlast the ~14 ms minimum delay phase plus part of the ramp
    goff = GON + 0.04
    r = run_geg(0.0, 0.3, 0.9, GON, goff, goff + 0.15)
    v = r["out2"]
    peak = v.max()
    assert 1.0 < peak < 4.5, f"partial-attack peak {peak:.2f} V"
    assert v[-1] < 0.3, "did not release back to the floor"
