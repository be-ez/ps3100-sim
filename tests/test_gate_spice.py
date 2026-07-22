"""Validate the KLM-69E gate/KORG35 netlist against hand theory (ngspice).

Covers, per the plan's KORG35 constraints:
  - small-signal first: exponential cutoff law over the FC control range,
    resonance and the cutoff-tracking input HPF (AC analysis)
  - envelope shaping: attack RC (C201 1u via the ~100k charge path) and
    release RC (4.7M bleed)
  - gate isolation (CD4007 pass device off)
  - large-signal onset: THD vs drive at three levels, H2-dominant asymmetry
    (the reverse-saturated Q2/Q3 junctions are the first devices to leave
    their pseudo-linear region; see netlists/klm69-gate.cir header)
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from analysis.ac_analysis import peak_metrics

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm69-gate.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

DEFAULTS = dict(
    Vfcu=-0.1371,
    Venv=10,
    Vkey=10,
    Ratk="100k",
    Rtrim=235,
    Rpeak="1e9",
    Asig=0.02,
    Fsig=500,
    Colork="100k",
    Vexp=0,  # EXPAND depth (per-note envelope->cutoff), 0 = off (baseline)
    Vrel=11.6,  # KLM-62D release terminal (11.6 full release .. 0.14 damped)
)

AC_CONTROL = """op
ac dec 100 10 100k
wrdata gate_ac.txt vdb(out) vdb(nodea) vdb(no) vdb(ni)"""


def _deck(params: dict, control: str | None = None, key_pulse: str | None = None) -> str:
    p = {**DEFAULTS, **params}
    line = ".param " + " ".join(f"{k}={v}" for k, v in p.items())
    deck = re.sub(r"^\.param .*$", line, NETLIST.read_text(), count=1, flags=re.MULTILINE)
    if control is not None:
        deck = deck.replace(AC_CONTROL, control)
    if key_pulse is not None:
        deck = deck.replace("Vkey key 0 DC {Vkey}", key_pulse)
    return deck


def _run(deck: str, outname: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            ["ngspice", "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out = tmpdir / outname
        if not out.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        return np.loadtxt(out)


def run_ac(**params) -> dict[str, np.ndarray]:
    """AC sweep; returns freq plus dB traces (out, nodea, no, ni)."""
    data = _run(_deck(params), "gate_ac.txt")
    res = {"freq": data[:, 0]}
    for i, name in enumerate(["out", "nodea", "no", "ni"]):
        res[name] = data[:, 2 * i + 1]
    return res


def run_tran(tstep, tstop, tstart=0.0, key_pulse=None, **params) -> np.ndarray:
    ctrl = f"""tran {tstep} {tstop} {tstart}
wrdata gate_tran.txt v(nodea) v(envc)"""
    return _run(_deck(params, control=ctrl, key_pulse=key_pulse), "gate_tran.txt")


def harmonics(t: np.ndarray, y: np.ndarray, f0: float, n: int = 3) -> list[float]:
    periods = int((t[-1] - t[0]) * f0)
    mask = t <= t[0] + periods / f0
    tt, yy = t[mask], y[mask] - np.mean(y[mask])
    span = tt[-1] - tt[0]
    return [
        abs(np.trapezoid(yy * np.exp(-2j * np.pi * k * f0 * tt), tt) * 2 / span)
        for k in range(1, n + 1)
    ]


# FC grid used throughout (same grid the DSP tables were fitted on): the
# open-circuit blended FCU/FCL bus, -0.48..+0.12 V step 0.12, covering the
# physical KLM-63 output range -0.47..+0.11 V. dsp/gate.dsp's vfc slider maps -14..0 onto
# this grid linearly: vfcu = 0.12 + vfc * (0.6 / 14).
VFCU_GRID = [-0.48, -0.36, -0.24, -0.12, 0.0, 0.12]


@pytest.fixture(scope="module")
def ac_grid():
    return {vfcu: run_ac(Vfcu=vfcu) for vfcu in VFCU_GRID}


def test_cutoff_exponential_in_fc(ac_grid):
    """The reverse-saturation splitter gives cutoff exponential in the bus
    voltage (Stinchcombe sec. 3.2). The pin-2 attenuation is 0.342 (14.5R
    bus source vs the 47R per-note return), so theory predicts
    0.342/17.9mV ~ 19 oct/V of bus voltage; the netlist realizes 6.8..17.3
    oct/V (top-end compression as the splitter saturates toward full
    current), monotone over 7.9 octaves (32 Hz..7.6 kHz)."""
    f0s = [peak_metrics(ac_grid[v]["freq"], ac_grid[v]["nodea"])["f0"] for v in VFCU_GRID]
    assert np.all(np.diff(f0s) > 0), f"cutoff not monotone in Vfcu: {f0s}"
    octs = np.diff(np.log2(f0s)) / np.diff(VFCU_GRID)
    assert np.all(octs > 5.0) and np.all(octs < 21.0), f"oct/V out of range: {octs}"
    assert np.log2(f0s[-1] / f0s[0]) > 7.0, "control range spans too few octaves"


def test_resonance_and_gain(ac_grid):
    """Fixed resonance from the Sallen-Key cap ratio (0.033u/0.001u) with the
    C301 series cap sharpening the peak; the buffer's gain deficit caps it.
    Netlist realizes a ~+13..18 dB peak, Q(-3 dB) ~ 7.6..14.6 over the four
    non-edge grid points (at -0.48 V the tracking HPF eats the peak)."""
    for vfcu in [-0.36, -0.24, -0.12, 0.0]:
        m = peak_metrics(ac_grid[vfcu]["freq"], ac_grid[vfcu]["nodea"])
        assert 10.0 < m["peak_db"] < 19.0, f"Vfcu={vfcu}: peak {m['peak_db']:.1f} dB"
        assert 5.0 < m["q"] < 20.0, f"Vfcu={vfcu}: Q {m['q']:.1f}"


def test_tracking_input_hpf(ac_grid):
    """C301 0.0022u against the cutoff-dependent module input resistance is a
    bass rolloff that tracks below f0: the response must fall well below the
    peak one octave under f0 (a plain 2-pole LP would be flat there).
    Measured drop 20.9..26.7 dB over the tested points."""
    for vfcu in [-0.36, -0.24, -0.12, 0.0]:
        r = ac_grid[vfcu]
        m = peak_metrics(r["freq"], r["nodea"])
        below = np.interp(m["f0"] / 2.0, r["freq"], r["nodea"])
        assert m["peak_db"] - below > 12.0, (
            f"Vfcu={vfcu}: only {m['peak_db'] - below:.1f} dB down at f0/2"
        )


@pytest.mark.parametrize("ratk_ohm,ratk", [(47e3, "47k"), (100e3, "100k")])
def test_envelope_attack_rc(ratk_ohm, ratk):
    """Attack: C201 1u charged through the (conditioned) ~Ratk path."""
    data = run_tran("1m", "600m", key_pulse="Vkey key 0 PULSE(0 10 10m 1u 1u 5 10)", Ratk=ratk)
    t, env = data[:, 0], data[:, 3]
    final = env[t > 5 * ratk_ohm * 1e-6 + 0.01].mean()
    t63 = t[np.argmax(env > 0.632 * final)] - 10e-3
    assert t63 == pytest.approx(ratk_ohm * 1e-6, rel=0.2), f"attack t63 {t63 * 1e3:.0f} ms"


def test_envelope_release_rc():
    """Release: C201 bleeds through R401 4.7M (tau ~4.7 s; D301 and the
    buffer paths add a few percent of extra leakage)."""
    data = run_tran("2m", "1500m", key_pulse="Vkey key 0 PULSE(0 10 0 1u 1u 300m 10)")
    t, env = data[:, 0], data[:, 3]
    m = (t > 0.32) & (t < 1.45)
    tt, ee = t[m], env[m]
    tau = -(tt[-1] - tt[0]) / np.log(ee[-1] / ee[0])
    assert 3.4 < tau < 5.6, f"release tau {tau:.2f} s vs RC 4.7 s"


def test_gate_isolation():
    """Note off (Venv=0): the CD4007 pass device and Q401 both open; only
    stray feedthrough (CFEED 0.5p) remains."""
    on = run_ac(Venv=10)
    off = run_ac(Venv=0)
    f0 = peak_metrics(on["freq"], on["nodea"])["f0"]
    lvl_on = np.interp(f0, on["freq"], on["nodea"])
    lvl_off = np.interp(f0, off["freq"], off["nodea"])
    assert lvl_on - lvl_off > 50.0, f"only {lvl_on - lvl_off:.1f} dB isolation"


def test_expand_baseline_off_unchanged():
    """EXPAND off (Vexp=0) must leave the filter identical to the pre-EXPAND
    deck: the series D301 is reverse/zero-biased and blocks the 33k, so the
    module input node keeps its bare high-Z impedance (and the C301 tracking
    HPF is untouched). f0/peak/Q at the default op are the documented baseline."""
    m = peak_metrics(*[run_ac(Vexp=0)[k] for k in ("freq", "nodea")])
    assert m["f0"] == pytest.approx(1597, rel=0.02), f"baseline f0 {m['f0']:.0f}"
    assert 13.0 < m["peak_db"] < 16.0 and 8.0 < m["q"] < 10.0


def test_expand_sweeps_cutoff_with_envelope():
    """EXPAND (the signature per-note pluck): the gate envelope, scaled by the
    depth Vexp, pulls the module input node down through R501 33k + D301 and
    the reverse-mode splitter operating point shifts so this note's cutoff
    climbs (BRIGHT pluck). Snapshot the cutoff at three envelope levels (via
    Venv, VCA open): f0 rises monotonically with the envelope, every point
    above the EXPAND-off baseline, ~+1.4 oct at full envelope for Vexp=0.5."""
    base = peak_metrics(*[run_ac(Vexp=0, Venv=10)[k] for k in ("freq", "nodea")])["f0"]
    f0s = [
        peak_metrics(*[run_ac(Vexp=0.5, Venv=v)[k] for k in ("freq", "nodea")])["f0"]
        for v in (7.0, 8.5, 10.0)
    ]
    assert np.all(np.diff(f0s) > 0), f"cutoff not monotone in envelope: {f0s}"
    assert f0s[0] > base, f"even partial envelope should brighten: {f0s[0]:.0f} vs {base:.0f}"
    depth = np.log2(f0s[-1] / base)
    assert 1.0 < depth < 1.9, f"full-envelope pluck depth {depth:.2f} oct (expect ~1.4)"


def test_expand_depth_monotone_and_bounded():
    """Depth control: cutoff rise is monotone in Vexp and stays in the graceful
    region (the cell keeps a resonant peak up to Vexp~0.7; the DSP maps
    expand=1 here)."""
    base = peak_metrics(*[run_ac(Vexp=0)[k] for k in ("freq", "nodea")])["f0"]
    prev = 0.0
    for vexp in (0.3, 0.5, 0.7):
        m = peak_metrics(*[run_ac(Vexp=vexp)[k] for k in ("freq", "nodea")])
        oct_up = np.log2(m["f0"] / base)
        assert oct_up > prev, f"Vexp={vexp}: {oct_up:.2f} not > {prev:.2f}"
        assert m["peak_db"] > 11.0, f"Vexp={vexp}: peak collapsed to {m['peak_db']:.1f} dB"
        prev = oct_up
    assert prev == pytest.approx(2.25, abs=0.4), f"Vexp=0.7 depth {prev:.2f} oct"


def test_release_bus_sets_release_rate():
    """Release-bus consumption: the KLM-62D "GATE RELEASE TERMINAL" (Vrel) is
    read by the Q313/Q301 damp. While the key is held the damp is inhibited so
    C201 charges to full for every Vrel (sustain preserved); after key-off the
    release tau is set by the terminal: +11.6V full release ~= 4.7s (R401 only), +8.0/+5.8V half damp tens
    of ms, +0.14V damped ~20ms. Monotone, and full-release matches the 4.7M RC."""
    pulse = "Vkey key 0 PULSE(0 10 0 1u 1u 200m 10)"

    def hold_and_tau(vrel):
        d = run_tran("1m", "1500m", key_pulse=pulse, Vrel=vrel)
        t, envc = d[:, 0], d[:, 3]
        peak = envc[(t > 0.15) & (t < 0.2)].mean()
        m = (t > 0.22) & (t < 1.45)
        tt, ee = t[m], envc[m]
        good = ee > 0.05 * max(ee[0], 0.1)
        tt, ee = tt[good], ee[good]
        tau = -(tt[-1] - tt[0]) / np.log(ee[-1] / ee[0])
        return peak, tau

    peaks, taus = zip(*[hold_and_tau(v) for v in (11.6, 8.0, 5.8, 0.14)])
    assert all(p == pytest.approx(peaks[0], rel=0.05) for p in peaks), (
        f"sustain not preserved across release states: {[round(p, 2) for p in peaks]}"
    )
    assert np.all(np.diff(taus) < 0), f"release tau not monotone in Vrel: {taus}"
    assert taus[0] == pytest.approx(4.7, rel=0.15), f"full-release tau {taus[0]:.2f}s vs 4.7s RC"
    assert 0.04 < taus[1] < 0.10, f"half-damp (8.0V) tau {taus[1] * 1e3:.0f} ms"
    assert taus[-1] < 0.03, f"damped tau {taus[-1] * 1e3:.0f} ms (expect ~20 ms)"


def test_saturation_onset_and_asymmetry():
    """Large-signal: the linear signal-current budget of the reverse-mode
    devices is the control current itself, so distortion onset scales with
    the cutoff. At the mid-law default (Vfcu=-0.1371, f0 ~ 1.6 kHz - a
    ~1.7x larger budget than the pre-rewire 917 Hz point) the measured THD
    trajectory at 5/20/100 mV is 0.021/0.109/0.366, monotone. H2 dominance
    (one-sided junction law) is asserted at LOW drive (measured 5x): at
    higher drives the resonance at 1.6 kHz sits on H3 of the 500 Hz tone
    (1.5 kHz) and pumps it toward H2's level."""
    thds, h2rel, h3rel = [], [], []
    for asig in [0.005, 0.02, 0.1]:
        d = run_tran("20u", "250m", tstart="50m", Asig=asig, Fsig=500)
        t, y = d[:, 0], d[:, 1]
        h = harmonics(t, y, 500.0)
        thds.append(np.sqrt(sum(x * x for x in h[1:])) / h[0])
        h2rel.append(h[1] / h[0])
        h3rel.append(h[2] / h[0])
    assert thds[0] < 0.05, f"5 mV drive should be near-clean: THD {thds[0]:.2f}"
    assert 0.05 < thds[1] < 0.3, f"20 mV drive moderately dirty: THD {thds[1]:.2f}"
    assert thds[2] > 0.25, f"100 mV drive heavily saturated: THD {thds[2]:.2f}"
    assert thds[0] < thds[1] < thds[2]
    # asymmetric (even-order) clipping dominates at low drive
    assert h2rel[0] > 3.0 * h3rel[0], f"H2 {h2rel[0]:.3f} vs H3 {h3rel[0]:.3f}"
