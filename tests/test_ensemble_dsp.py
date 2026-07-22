"""Compare dsp/ensemble.dsp against the KLM-76 SPICE reference.

The MN3004 BBD is clock-rate-dependent, so instead of one impulse
comparison the wet path is validated at frozen-LFO snapshots: each snapshot
phase maps (through the SPICE-fitted astable law shared by the DSP and
test_ensemble_spice.DSP_CONST) to a fixed clock rate / delay; ngspice gets
the delay as the behavioral T-line TD, the DSP gets the frozen phase, and
the full-mix magnitude responses (dry + both wet combs) must agree.

Also checks: single-channel comb-notch alignment (sub-0.1% delay accuracy),
the Faust LFO rates and the tau law via the monitor test hooks, and bypass
transparency."""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, build_driver, dsp_response_db, render
from tests.test_ensemble_spice import DSP_CONST, run_audio_ac, tau_of

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

# Frozen-LFO snapshots (phase in turns): delay extremes, center, and an
# asymmetric mid pair
SNAPSHOTS = [(0.0, 0.0), (0.25, 0.75), (0.4, 0.15)]

FMIN, FMAX = 30.0, 10_000.0
# tiered tolerances by depth below the response maximum. The comb-notch
# flanks amplify small wet-arm phase errors: bilinear phase warp of the
# ladder/pre-emphasis sections shifts the effective notch positions by a few
# Hz at 4-8 kHz (verified < 0.05% by test_notch_alignment below), and on a
# ~150-250 Hz comb spacing that is worth several dB right on a steep flank
# while the mean magnitude error stays < 0.05 dB. Measured worst cases at
# 48k (re-read clock law): 0.69 / 1.25 / 2.37 dB.
MASK_TOL_DB = [(10.0, 0.9), (15.0, 1.5), (20.0, 2.6)]


@pytest.fixture(scope="session")
def ensemble_bin() -> Path:
    return build_driver(REPO / "dsp" / "ensemble.dsp", "ensemble_ir")


@pytest.mark.parametrize("pa,pb", SNAPSHOTS)
def test_dsp_matches_spice_snapshots(ensemble_bin, pa, pb):
    t1, t2 = tau_of(pa), tau_of(pb)
    res = run_audio_ac(t1, t2)
    f, spice_db = res["freq"], res["out"]
    ir = render(ensemble_bin, "lfo_freeze=1", f"lfo_phase_a={pa}", f"lfo_phase_b={pb}")
    dsp_f, dsp_db = dsp_response_db(ir)
    dsp_on_f = np.interp(f, dsp_f, dsp_db)
    base = (f >= FMIN) & (f <= FMAX)
    for mask_db, tol in MASK_TOL_DB:
        m = base & (spice_db > spice_db.max() - mask_db)
        err = np.abs(dsp_on_f[m] - spice_db[m])
        worst = int(np.argmax(err))
        assert err.max() < tol, (
            f"phases ({pa},{pb}), mask {mask_db} dB: "
            f"error {err.max():.2f} dB at {f[m][worst]:.0f} Hz"
        )


def minima(f: np.ndarray, db: np.ndarray, flo: float, fhi: float) -> np.ndarray:
    """Comb-notch frequencies (parabolic interpolation), >= 6 dB deep."""
    m = (f >= flo) & (f <= fhi)
    fm, dm = f[m], db[m]
    idx = np.where((dm[1:-1] < dm[:-2]) & (dm[1:-1] < dm[2:]))[0] + 1
    med = np.median(dm)
    out = []
    for i in idx:
        if dm[i] < med - 6.0 and 0 < i < len(fm) - 1:
            y0, y1, y2 = dm[i - 1], dm[i], dm[i + 1]
            den = y0 - 2 * y1 + y2
            delta = 0.5 * (y0 - y2) / den if den != 0 else 0.0
            out.append(fm[i] + delta * (fm[i + 1] - fm[i]))
    return np.array(out)


def test_notch_alignment_single_channel(ensemble_bin):
    """Channel 1 comb against SPICE with channel 2 muted both sides: notch
    frequencies pin the implemented delay (and the arm phase) to sub-0.1%.
    Measured worst offset 1.5 Hz at 48k (Lagrange-5 fractional delay)."""
    pa = 0.6
    tau = tau_of(pa)
    res = run_audio_ac(tau, tau, g1=1.0, g2=1e-6, npts=12000)
    spice_n = minima(res["freq"], res["out"], 400, 4000)
    ir = render(ensemble_bin, "lfo_freeze=1", f"lfo_phase_a={pa}", "g2=0")
    dsp_f, dsp_db = dsp_response_db(ir)
    dsp_n = minima(dsp_f, dsp_db, 400, 4000)
    assert len(spice_n) >= 15
    for fsp in spice_n:
        fd = dsp_n[np.argmin(np.abs(dsp_n - fsp))]
        assert abs(fd - fsp) < 3.0, f"notch at {fsp:.1f} Hz off by {fd - fsp:.2f} Hz"


def monitor_trace(binary: Path, mon: int, n: int, *args: str) -> np.ndarray:
    out = subprocess.run(
        [str(binary), f"n={n}", f"fs={FS}", f"monitor={mon}", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return np.array(out.split(), dtype=float)


@pytest.mark.parametrize("mon,key", [(1, "lfo_fa"), (3, "lfo_fb")])
def test_faust_lfo_rates(ensemble_bin, mon, key):
    """The running Faust LFOs (monitor taps) reproduce the SPICE-measured
    ring rates; the SPICE side of the same constants is pinned by
    test_ensemble_spice.test_lfo_*."""
    n = 1 << 18  # 5.46 s at 48k
    v = monitor_trace(ensemble_bin, mon, n)
    v = v - v.mean()  # remove the +7.45 V DC (FM node is DC-coupled, U6)
    idx = np.where((v[:-1] < 0) & (v[1:] >= 0))[0]
    t = idx + (-v[idx]) / (v[idx + 1] - v[idx])
    freq = (len(t) - 1) / ((t[-1] - t[0]) / FS)
    assert freq == pytest.approx(DSP_CONST[key], rel=0.002)


def test_faust_tau_law(ensemble_bin):
    """Frozen-phase tau monitors match the shared clock law exactly."""
    args = ["lfo_freeze=1", "lfo_phase_a=0.25", "lfo_phase_b=0.75"]
    t1 = monitor_trace(ensemble_bin, 2, 256, *args)[-1] * 1e-3
    t2 = monitor_trace(ensemble_bin, 4, 256, *args)[-1] * 1e-3
    assert t1 == pytest.approx(tau_of(0.25), rel=1e-4)
    assert t2 == pytest.approx(tau_of(0.75), rel=1e-4)


def test_bypass_is_transparent(ensemble_bin):
    ir = render(ensemble_bin, "bypass=1", n=4096)
    assert ir[0] == pytest.approx(1.0)
    assert np.abs(ir[1:]).max() < 1e-12
