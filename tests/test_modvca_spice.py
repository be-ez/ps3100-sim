"""ngspice validation of the KLM-63 MOD-VCA + MG2 netlist against hand theory.

Referee chain (same philosophy as the resonator): the netlist
 is the reference; this module
checks it against closed-form circuit theory:

  - MOD-VCA control law: LED current vs the emitter-network theory
    I_led ~= (V(n6) - Vbe + 0.438) * (1/1k + 1/33k), dark floor, Q201
    saturation plateau at the top of the pot, monotonicity.
  - MOD-VCA signal path: flat response, gain = 0.75 * VR201/(Rldr + 2.2k)
    at the LDR resistance the drive law produced.
  - MG2: oscillation, frequency vs the asymmetric-slope triangle-core
    theory, amplitude, symmetry, and triangle crest factor.

The helpers (run_klm63, cv_law_grid, mg2_transient) are imported by
tests/test_modvca_dsp.py so the DSP referee reuses the same SPICE runs.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm63-modvca-mg2.cir"
VACTROL_LIB = REPO / "netlists" / "models" / "vactrol.lib"

# defaults of the netlist .param line
DEFAULTS = dict(mcv=0.5, rate=0.5, rsig=47e3, vr201=10e3, vind1=0.0)

# constants shared with the netlist (model cards / opamp subckt)
VSAT = 13.5  # opamp tanh saturation
VCE202 = 0.019  # Q202 deep-saturation Vce (measured, see model doc)
R213, R215, C201 = 1e6, 510e3, 0.022e-6
SCHMITT_TH = VSAT * 51.0 / 151.0  # R217/R218 divider
PIN27_PAD = 1.5e3 / (1e3 + 10.0 + 1.5e3)  # R221(+opamp Ro)/R222

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")


def run_klm63(control: str, out_name: str, **params) -> np.ndarray:
    """Run the netlist with a substituted .param line and .control block.

    Returns the wrdata array: column 0 is the sweep variable, values are in
    columns 1, 3, 5, ... (wrdata repeats the x column per trace).
    """
    p = {**DEFAULTS, **params}
    pline = ".param " + " ".join(f"{k}={v:g}" for k, v in p.items())
    deck = NETLIST.read_text()
    deck = re.sub(r"^\.param .*$", pline, deck, count=1, flags=re.MULTILINE)
    deck = deck.replace(".include models/vactrol.lib", f".include {VACTROL_LIB}")
    deck = re.sub(r"^\.control$.*?^\.endc$", control, deck, flags=re.DOTALL | re.MULTILINE)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            ["ngspice", "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out_file = tmpdir / out_name
        if not out_file.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        return np.loadtxt(out_file)


@functools.lru_cache(maxsize=1)
def cv_law_grid() -> dict[str, np.ndarray]:
    """DC sweep of the MOD VCA CONT pot: pos -> Rldr, I_led, V(pin29), V(n6)."""
    data = run_klm63(
        ".control\ndc VPOS 0 1 0.01\nwrdata law.txt v(rldr) i(VSF) v(p29) v(n6)\n.endc",
        "law.txt",
    )
    return {
        "pos": data[:, 0],
        "rldr": data[:, 1],
        "iled": data[:, 3],
        "v29": data[:, 5],
        "vn6": data[:, 7],
    }


@functools.lru_cache(maxsize=None)
def vca_ac(rsig: float) -> dict[str, np.ndarray]:
    """AC sweep of the signal path at a fixed LDR resistance."""
    data = run_klm63(
        ".control\nac dec 20 20 20k\nwrdata ac.txt vdb(p31)\n.endc", "ac.txt", rsig=rsig
    )
    return {"freq": data[:, 0], "db": data[:, 1]}


@functools.lru_cache(maxsize=None)
def mg2_transient(rate: float, tstop: float, step: float) -> dict[str, np.ndarray]:
    """MG2 transient at a FREQ CONT pot position; returns t, nTri, p27, nSq."""
    data = run_klm63(
        f".control\ntran {step:g} {tstop:g} 0 {step:g}\n"
        "wrdata tran.txt v(nTri) v(p27) v(nSq)\n.endc",
        "tran.txt",
        rate=rate,
    )
    return {"t": data[:, 0], "tri": data[:, 1], "p27": data[:, 3], "sq": data[:, 5]}


def vca_gain_db(rldr: float, vr201: float = 10e3) -> float:
    """Hand theory: two inverting stages, 0.75 * VR201/(Rldr + 2.2k), into
    the netlist's high-Z output placeholder (470R vs 1M)."""
    return 20 * np.log10(0.75 * vr201 / (rldr + 2.2e3)) + 20 * np.log10(1e6 / (1e6 + 470.0))


def mg2_theory(rate: float) -> dict[str, float]:
    """Asymmetric triangle-core theory (mirrors dsp/modvca.dsp's mg2 block)."""
    rtop, rbot = max(10e3 * (1 - rate), 1.0), max(10e3 * rate, 1.0)
    g26 = 1 / rtop + 1 / rbot + 1 / 15e3
    ka = (1 / R213 + 1 / R215) / 2
    ga = 1 / 15e3 + 1 / 1e6 + ka
    det = g26 * ga - (1 / 15e3) ** 2
    na = (g26 * 14.9 / 1e6 + (10.0 / rtop) / 15e3) / det
    sd = (na / 2) / R213 / C201
    su = ((na / 2 - VCE202) / R215) / C201 - sd
    freq = 1.0 / (2 * SCHMITT_TH * (1 / su + 1 / sd))
    duty_up = (1 / su) / (1 / su + 1 / sd)
    return {"na": na, "freq": freq, "duty_up": duty_up}


def resample(t: np.ndarray, x: np.ndarray, settle: float = 1.0, dt: float = 1e-3) -> np.ndarray:
    """Uniform resample of a transient trace (ngspice wrdata emits the raw
    solver timepoints, which cluster around switching edges and would bias
    sample statistics like RMS or rising-sample fractions)."""
    grid = np.arange(settle, t[-1], dt)
    return np.interp(grid, t, x)


def measure_freq(t: np.ndarray, x: np.ndarray, settle: float = 1.0) -> float:
    m = t > settle
    tt, xx = t[m], x[m]
    idx = np.where((xx[:-1] < 0) & (xx[1:] >= 0))[0]
    assert len(idx) >= 3, "not oscillating (fewer than 3 rising zero crossings)"
    tc = tt[idx] - xx[idx] * (tt[idx + 1] - tt[idx]) / (xx[idx + 1] - xx[idx])
    return 1.0 / np.diff(tc).mean()


# ---------------------------------------------------------------- MOD-VCA


def test_vca_dark_floor():
    """Pot fully down: LED off, photocell at the 1M dark clamp, gain floor
    0.75*10k/1.0022M ~= -42.5 dB."""
    law = cv_law_grid()
    assert law["rldr"][0] == pytest.approx(1e6, rel=1e-6)
    assert vca_gain_db(law["rldr"][0]) == pytest.approx(-42.52, abs=0.1)


def test_vca_law_monotonic_and_range():
    """Rldr falls monotonically with the pot until Q201 saturates near the
    top, where the LED current dips slightly past its peak (real physics:
    deeper saturation robs a little collector current) -- allow only a small
    creep there. Lit end lands ~3.2k (~6.8 mA through the P873 power law)."""
    law = cv_law_grid()
    active = law["pos"] <= 0.85
    assert np.all(np.diff(law["rldr"][active]) <= 1e-6)
    assert law["rldr"][-1] == pytest.approx(law["rldr"].min(), rel=0.02)
    assert 3.0e3 < law["rldr"][-1] < 3.5e3


def test_led_current_matches_emitter_theory():
    """Scan-doc theory: I_led ~= (V(n6) - Vbe + 0.438) * (1/1k + 1/33k) in the
    active region (0.438 V = R209's 33k/-14.9V offset at the emitter).
    Vbe taken constant at 0.68 V; tolerance covers its 0.66..0.71 V drift."""
    law = cv_law_grid()
    ge = 1 / 1e3 + 1 / 33e3
    ve_off = 14.9 / 33e3 / ge
    mid = (law["pos"] >= 0.15) & (law["pos"] <= 0.85)
    theory = (law["vn6"][mid] - 0.68 + ve_off) * ge
    assert np.abs(law["iled"][mid] / theory - 1).max() < 0.05


def test_q201_saturates_at_pot_top():
    """Near the top of the pot Q201 saturates: LED current plateaus at
    ~6.8 mA instead of following the base voltage up."""
    law = cv_law_grid()
    i95 = np.interp(0.95, law["pos"], law["iled"])
    i100 = law["iled"][-1]
    assert 6e-3 < i100 < 7.5e-3
    assert i100 == pytest.approx(i95, rel=0.03)
    # while the unloaded pot voltage keeps rising 9 -> 10 V
    assert law["v29"][-1] > law["v29"][np.searchsorted(law["pos"], 0.95)] + 0.5


@pytest.mark.parametrize("pos", [0.1, 0.3, 0.5, 0.8])
def test_vca_gain_matches_theory(pos):
    """Signal path at the DC-sweep operating point: gain = 0.75*VR201/(R+2.2k)
    and flat across the audio band (no reactive parts on the sheet)."""
    law = cv_law_grid()
    rsig = float(np.interp(pos, law["pos"], law["rldr"]))
    ac = vca_ac(rsig)
    g1k = float(np.interp(1e3, ac["freq"], ac["db"]))
    assert g1k == pytest.approx(vca_gain_db(rsig), abs=0.05)
    assert ac["db"].max() - ac["db"].min() < 0.02


# ------------------------------------------------------------------- MG2


@pytest.fixture(scope="module")
def mg2_slow():
    return mg2_transient(0.1, 8.0, 1e-3)


@pytest.fixture(scope="module")
def mg2_fast():
    return mg2_transient(1.0, 3.0, 0.5e-3)


def test_mg2_frequency_matches_theory(mg2_slow, mg2_fast):
    """Triangle-core rate: f = 1/(2*TH*(1/s_up + 1/s_dn)) with the rate-node
    and slope formulas from the netlist header. 2% covers comparator edge
    effects and Q202's current-dependent saturation voltage."""
    for run, rate in [(mg2_slow, 0.1), (mg2_fast, 1.0)]:
        f = measure_freq(run["t"], run["tri"])
        assert f == pytest.approx(mg2_theory(rate)["freq"], rel=0.02)


def test_mg2_rate_pot_spans_useful_lfo_range(mg2_slow, mg2_fast):
    """FREQ CONT sweeps roughly a decade, ~1.4 Hz at 10% to ~12 Hz at max
    (theory: ~0.3 Hz at pot zero via the R212 bias)."""
    f_slow = measure_freq(mg2_slow["t"], mg2_slow["tri"])
    f_fast = measure_freq(mg2_fast["t"], mg2_fast["tri"])
    assert 1.0 < f_slow < 2.0
    assert 10.0 < f_fast < 14.0
    assert f_fast / f_slow > 7.0


def test_mg2_amplitude(mg2_slow):
    """Triangle peaks at the Schmitt thresholds +/-Vsat*51/151 ~= 4.56 V,
    padded by R221/R222 to ~2.73 V at pin 27. 3% covers switching overshoot
    and the opamp's 10R output resistance."""
    m = mg2_slow["t"] > 1.0
    tri, p27 = mg2_slow["tri"][m], mg2_slow["p27"][m]
    assert tri.max() == pytest.approx(SCHMITT_TH, rel=0.03)
    assert -tri.min() == pytest.approx(SCHMITT_TH, rel=0.03)
    assert p27.max() == pytest.approx(SCHMITT_TH * PIN27_PAD, rel=0.03)


def test_mg2_triangle_shape(mg2_slow, mg2_fast):
    """Crest factor of an ideal triangle is sqrt(3); duty (rising fraction)
    follows the slight up/down slope asymmetry from Q202's Vce(sat)."""
    for run, rate in [(mg2_slow, 0.1), (mg2_fast, 1.0)]:
        tri = resample(run["t"], run["tri"])
        crest = np.abs(tri).max() / np.sqrt((tri**2).mean())
        assert crest == pytest.approx(np.sqrt(3.0), rel=0.03)
        rising = np.diff(tri) > 0
        duty = rising.mean()
        assert duty == pytest.approx(mg2_theory(rate)["duty_up"], abs=0.03)
