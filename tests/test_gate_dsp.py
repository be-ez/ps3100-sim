"""Compare dsp/gate.dsp against the KLM-69E netlist (ngspice referee).

Small-signal first (plan's KORG35 constraint): the DSP's fitted linear core
(tracking HPF + resonant 2-pole, 2x-oversampled) must match the SPICE AC
magnitude response across the FC control range. Then large-signal: the
offset-tanh core saturator must reproduce the SPICE transient harmonic
trajectory (H2-dominant onset) at three drive levels. Finally the envelope
gate: attack/release behavior and click-free switching.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from analysis.ac_analysis import peak_metrics
from tests.test_dsp_vs_spice import FS, build_driver, dsp_response_db, render
from tests.test_gate_spice import VFCU_GRID, harmonics, run_ac, run_tran

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

# the DSP vfc slider (-14..0, panel-volt feel) maps linearly onto the
# open-circuit bus grid: vfcu = 0.12 + vfc * (0.6 / 14), so the SPICE grid
# points correspond to these slider values (28/6 = 2.8 V steps)
SLIDER_FOR = {vfcu: round((vfcu - 0.12) * 14.0 / 0.6, 1) for vfcu in VFCU_GRID}

# tolerances. The linear core is a 4-parameter per-gridpoint fit of the
# SPICE curve (residual <=0.10 dB except the -0.48 V edge at 0.68 dB),
# fitted directly against the 2x-DISCRETIZED cascade over this window - so
# unlike the old continuous-time fit there is no residual warp gap left:
# measured worst errors 0.01..0.03 dB in the peak region, 0.04..0.10 dB
# overall. Tolerances kept at the historical values (they now carry ~10x
# margin instead of ~1.3x).
PEAK_TOL_DB = 0.75
SKIRT_TOL_DB = 2.0
FMIN, FMAX = 20.0, 12_000.0


@pytest.fixture(scope="session")
def gate_bin() -> Path:
    return build_driver(REPO / "dsp" / "gate.dsp", "gate_ir")


@pytest.mark.parametrize("vfcu", [v for v in VFCU_GRID if v >= -0.36])
def test_small_signal_matches_spice(gate_bin, vfcu):
    """DSP linear core (bypass_nl: SPICE AC is linearized too; the driver's
    unit impulse is ~300x the core clip scale) vs ngspice, absolute dB.
    The -0.48 V edge is excluded as before (f0 falls to 32 Hz and the
    tracking HPF eats the peak; fit residual there is 0.79 dB)."""
    ir = render(gate_bin, "bypass_env=1", "bypass_nl=1", f"vfc={SLIDER_FOR[vfcu]}")
    dsp_f, dsp_db = dsp_response_db(ir)

    spice = run_ac(Vfcu=vfcu)
    f, sdb = spice["freq"], spice["nodea"]
    mask = (f >= FMIN) & (f <= FMAX) & (sdb > sdb.max() - 35.0)
    ff, ss = f[mask], sdb[mask]
    dd = np.interp(np.log10(ff), np.log10(dsp_f[1:]), dsp_db[1:])
    err = np.abs(dd - ss)

    worst = int(np.argmax(err))
    assert err[worst] < SKIRT_TOL_DB, f"Vfcu={vfcu}: {err[worst]:.2f} dB at {ff[worst]:.0f} Hz"

    f0 = ff[np.argmax(ss)]
    band = (ff >= 0.7 * f0) & (ff <= 1.4 * f0)
    assert err[band].max() < PEAK_TOL_DB, (
        f"Vfcu={vfcu}: peak region off by {err[band].max():.2f} dB"
    )


def _dsp_harmonics(gate_bin, amp, freq=500.0, vfc=-6):
    y = render(
        gate_bin,
        "bypass_env=1",
        f"vfc={vfc}",
        f"testosc_amp={amp}",
        f"testosc_freq={freq}",
        n=1 << 16,
    )
    y = y[len(y) // 2 :]  # steady state
    t = np.arange(len(y)) / FS
    return harmonics(t, y, freq)


def test_large_signal_harmonics_match_spice(gate_bin):
    """Transient comparison at three drive levels spanning clean to heavily
    saturated (plan requirement), at the mid-law default (vfc=-6 slider =
    Vfcu=-0.1371, f0 ~ 1.6 kHz; drives rescaled 2/10/50 -> 5/20/100 mV with
    the rewired FC interface's larger control-current budget, and nlA refit
    0.0035 -> 0.0095). The offset-tanh matches H1 and the H2-dominant onset
    closely through moderate drive (measured: H1 within 1%, H2/H1 within
    2..10%); deep saturation (100 mV) is only bounded loosely: the SPICE
    cell's current-starved junctions generate resonance-pumped H3 and
    fundamental compression that a single static shaper does not reproduce
   ."""
    for asig in [0.005, 0.02]:
        d = run_tran("20u", "250m", tstart="50m", Asig=asig, Fsig=500)
        hs = harmonics(d[:, 0], d[:, 1], 500.0)
        hd = _dsp_harmonics(gate_bin, asig)
        assert hd[0] / hs[0] == pytest.approx(1.0, abs=0.2), (
            f"{asig} V: fundamental {hd[0]:.4g} vs SPICE {hs[0]:.4g}"
        )
        assert hd[1] / hd[0] == pytest.approx(hs[1] / hs[0], rel=0.25), (
            f"{asig} V: H2/H1 {hd[1] / hd[0]:.3f} vs SPICE {hs[1] / hs[0]:.3f}"
        )
    # deep saturation: both heavily distorted (measured H2/H1 0.37 DSP vs
    # 0.34 SPICE); DSP allowed 0.5x..3x on H1
    d = run_tran("20u", "250m", tstart="50m", Asig=0.1, Fsig=500)
    hs = harmonics(d[:, 0], d[:, 1], 500.0)
    hd = _dsp_harmonics(gate_bin, 0.1)
    assert hd[1] / hd[0] > 0.25 and hs[1] / hs[0] > 0.25
    assert 0.5 < hd[0] / hs[0] < 3.0


def _dsp_peak_hz(gate_bin, expand, force):
    """Peak frequency of the DSP linear core with a forced EXPAND envelope
    level (expand_force lets the impulse driver see a steady swept cutoff)."""
    ir = render(
        gate_bin,
        "bypass_env=1",
        "bypass_nl=1",
        "vfc=-6",
        f"expand={expand}",
        f"expand_force={force}",
    )
    f, db = dsp_response_db(ir)
    m = (f >= 20.0) & (f <= 20_000.0)
    return f[m][np.argmax(db[m])]


def test_expand_cutoff_sweep_matches_spice(gate_bin):
    """EXPAND per-note pluck (headline gap): the DSP's envelope->cutoff sweep
    must land on the ngspice-measured cutoff. Snapshot the peak frequency at
    three effective injection levels (expand=1, forced envelope L -> the SPICE
    Vexp = L*uMax) at the default op (vfc=-6). The DSP peak tracks the SPICE
    f0 within ~3% at every point, and the sweep is monotone (brighter with
    more envelope). uMax=0.7 so L maps to Vexp=0.7*L."""
    peaks = []
    for L, vexp in [(0.0, 0.0), (0.5, 0.35), (0.714, 0.5), (1.0, 0.7)]:
        spice_f0 = peak_metrics(*[run_ac(Vexp=vexp)[k] for k in ("freq", "nodea")])["f0"]
        dsp_f0 = _dsp_peak_hz(gate_bin, 1.0, L)
        assert dsp_f0 / spice_f0 == pytest.approx(1.0, abs=0.08), (
            f"L={L} (Vexp={vexp}): DSP peak {dsp_f0:.0f} Hz vs SPICE {spice_f0:.0f} Hz"
        )
        peaks.append(dsp_f0)
    assert np.all(np.diff(peaks) > 0), f"DSP EXPAND sweep not monotone: {peaks}"
    # full-depth pluck brightens by well over an octave at this operating point
    assert np.log2(peaks[-1] / peaks[0]) > 1.5, (
        f"full pluck only {np.log2(peaks[-1] / peaks[0]):.2f} oct"
    )


def test_expand_off_is_baseline(gate_bin):
    """expand=0 (or a zero envelope) must leave the linear core bit-identical to
    the EXPAND-off response: 2^0 = 1 exactly, so instrument.dsp's envelope-free
    path is untouched."""
    a = render(gate_bin, "bypass_env=1", "bypass_nl=1", "vfc=-6")
    b = render(gate_bin, "bypass_env=1", "bypass_nl=1", "vfc=-6", "expand=1", "expand_force=0")
    assert np.allclose(a, b, atol=1e-12), "EXPAND at zero envelope changed the output"


def test_distortion_monotone_in_drive(gate_bin):
    h2rel = []
    for amp in [0.005, 0.02, 0.1]:
        h = _dsp_harmonics(gate_bin, amp)
        h2rel.append(h[1] / h[0])
    assert h2rel[0] < h2rel[1] < h2rel[2]


def test_envelope_gate_attack_release(gate_bin):
    """Gate on at N/2 with the test oscillator running: silence before,
    sound after, with the VCA opening as the attack RC crosses the CD4007
    threshold (~0.9*tau); release run: sound dies ~0.53*tau after gate-off
    (envelope falls from 1.0 to the 0.59 conduction threshold)."""
    n = 1 << 16
    half = n // 2

    y = render(gate_bin, "testosc_amp=0.005", "vfc=-6", "attack=0.05", f"step:gate={half}:1", n=n)
    pre = np.sqrt(np.mean(y[:half] ** 2))
    post = np.sqrt(np.mean(y[int(half + 0.2 * FS) :] ** 2))
    assert pre < 1e-7, "output before gate-on should be silent"
    assert post > 1e-4, "output after gate-on should sound"
    # VCA opens near t = 0.891*attack (one-pole reaching the 0.59 threshold)
    amp_t = np.abs(y[half:])
    i10 = np.argmax(amp_t > 0.1 * amp_t.max())
    t_open = i10 / FS
    assert 0.5 * 0.0891 < t_open < 2.0 * 0.0891, f"VCA opened at {t_open * 1e3:.1f} ms"

    y = render(
        gate_bin,
        "testosc_amp=0.005",
        "vfc=-6",
        "gate=1",
        "release=1.0",
        f"step:gate={half}:0",
        n=n,
    )
    t_cut = 0.527  # release tau * ln(1/0.59)
    alive = np.sqrt(np.mean(y[half + int(0.3 * FS) : half + int(0.4 * FS)] ** 2))
    dead = np.sqrt(np.mean(y[half + int(0.62 * FS) :] ** 2))
    assert alive > 1e-4, "should still sound 0.3 s into a 1 s release"
    assert dead < 0.01 * alive, f"should be cut well after t={t_cut:.2f} s"


def test_gate_switching_is_click_free(gate_bin):
    """The envelope RC turns the CD4007's ~0.3 V switching window into a
    ms-scale ramp: no output discontinuity beyond the signal's own slew."""
    n = 1 << 16
    half = n // 2
    y = render(gate_bin, "testosc_amp=0.02", "vfc=-6", "attack=0.05", f"step:gate={half}:1", n=n)
    peak = np.abs(y).max()
    jumps = np.abs(np.diff(y))
    # a 500 Hz sine of amplitude A slews at most 2*pi*500/FS*A ~= 0.065*A
    # per sample; allow 3x for the resonant filter's transient
    assert jumps.max() < 0.2 * peak, f"click: jump {jumps.max():.3g} vs peak {peak:.3g}"
