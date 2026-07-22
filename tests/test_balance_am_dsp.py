"""Compare dsp/balance_am.dsp against the SPICE reference
(netlists/klm62d-balance-am.cir), with ngspice as the referee.

Two comparison methods:

1. Mixer/static path: the DSP is linear when the JFET channel-voltage
   correction is off ("corr=0") and no modulation is applied, so the offline
   impulse driver's FFT is the frequency response; compare against the
   netlist's AC sweep at pin 26 over 30 Hz..10 kHz for several balance-pot
   positions, bias trims and intensity settings.

2. AM/multiplier path: a two-tone transient - carrier into the upper mixer
   channel, modulator into AM MOD IN - rendered by both simulators on the
   same 48 kHz grid (ngspice's adaptive-step output is resampled). Carrier,
   first and second sideband amplitudes are extracted by projection over an
   integer number of cycles and compared in dB. The DSP runs its per-sample
   companion-model network solve with the channel correction on ("corr=1"),
   which is what makes the 2nd-order sidebands track SPICE.

All test settings sit in the stable region of the intensity-pot feedback
loop.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.test_balance_am_spice import (
    DEFAULTS,
    FS_TRAN,
    TSTOP,
    dbv,
    run_ac,
    run_tran,
    tone_amp,
)
from tests.test_dsp_vs_spice import build_driver, render

REPO = Path(__file__).resolve().parent.parent
FS = FS_TRAN  # 48 kHz, shared with the SPICE resampling grid
# IR length must cover the slowest network mode: the balance-pot leg RC
# (10u into ~100k, ~1 s tail) leaves a 0.2 dB LF truncation error at 2^16
# samples; 2^18 (5.5 s) brings it below 0.01 dB.
N_IR = 1 << 18

# Tolerances. The DSP solves the same network as the netlist with SPICE's own
# trapezoidal cap discretization, so the static match is limited only by the
# deliberately omitted small elements (C303's 0.17 Hz corner, the C302/VR304
# cancel feed, JFET junction caps): measured residual <= 0.007 dB, worst
# at ~39 Hz when the balance pot slams one 10u leg to the wiper (72 Hz
# corner).
FR_TOL_DB = 0.15
FMIN, FMAX = 30.0, 10_000.0
# Two-tone: carrier and first sidebands measured within 0.02 dB; the ring
# case's nulled carrier is a ~-55 dBV residual of two cancelling paths and
# inherits their small mismatches, so it gets a looser bound.
TONE_TOL_DB = 0.25
RING_CARRIER_TOL_DB = 0.6

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)


@pytest.fixture(scope="session")
def balance_am_bin() -> Path:
    return build_driver(REPO / "dsp" / "balance_am.dsp", "balance_am_ir")


def static_args(p: dict) -> list[str]:
    return [
        f"bal={p['bal']}",
        f"lvl={p['lvl']}",
        f"bias={p['bias']}",
        f"rbal={p['rbal']}",
        f"intensity={p['intensity']}",
        f"input_sel={1 if p['acl'] else 0}",
    ]


FR_CASES = [
    dict(),  # factory-ish AM operating point
    dict(bal=0.0),  # pot slammed low: 72 Hz C203 corner in-band
    dict(bal=0.25),
    dict(bal=0.75),
    dict(bal=1.0),  # pot slammed high: C201 corner
    dict(bias=0.35),  # hot JFET path (gain ~3 through the AM)
    dict(bias=0.50),  # pinched off: dry/ring path only
    dict(bias=0.50, intensity=0.02),  # intensity rheostat nearly closed
    dict(bias=0.43, intensity=0.2),  # past the ring null
    dict(acu=0, acl=1, bal=0.25),  # lower mixer channel
]


@pytest.mark.parametrize(
    "over", FR_CASES, ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()) or "defaults"
)
def test_dsp_matches_spice_fr(balance_am_bin, over):
    p = dict(DEFAULTS, **over)
    ir = render(balance_am_bin, *static_args(p), "corr=0", "ttones=0", n=N_IR)
    h = np.fft.rfft(ir)
    fax = np.fft.rfftfreq(N_IR, 1.0 / FS)
    dsp_db = 20 * np.log10(np.maximum(np.abs(h), 1e-15))

    spice = run_ac(**over)
    m = (spice["freq"] >= FMIN) & (spice["freq"] <= FMAX)
    f = spice["freq"][m]
    dsp_on_f = np.interp(np.log10(f), np.log10(fax[1:]), dsp_db[1:])
    err = np.abs(dsp_on_f - spice["out26"][m])
    worst = int(np.argmax(err))
    assert err[worst] < FR_TOL_DB, f"error {err[worst]:.3f} dB at {f[worst]:.0f} Hz"


TONES = {"carrier": 2000.0, "lsb": 1800.0, "usb": 2200.0, "lsb2": 1600.0, "usb2": 2400.0}
TT_CASES = [
    ("am", dict(amod=2.5)),  # deep AM: gate swing ~0.55 of the overdrive
    ("am_small", dict(amod=0.5)),  # near-linear AM
    ("ring", dict(amod=2.5, bias=0.4110)),  # carrier nulled: balanced modulation
]


def dsp_two_tone(binary: Path, p: dict) -> np.ndarray:
    n = int(TSTOP * FS)
    return render(
        binary,
        *static_args(p),
        "corr=1",
        "ttones=1",
        f"fcar={p['fcar']}",
        f"acar={p['acar']}",
        f"fmod_hz={p['fmod']}",
        f"amod={p['amod']}",
        n=n,
    )


@pytest.mark.parametrize("name,over", TT_CASES, ids=[c[0] for c in TT_CASES])
def test_dsp_matches_spice_two_tone(balance_am_bin, name, over):
    p = dict(DEFAULTS, **over)
    v_spice = run_tran(**over)
    v_dsp = dsp_two_tone(balance_am_bin, p)
    for lab, f0 in TONES.items():
        a_sp = tone_amp(v_spice, f0)
        a_ds = tone_amp(v_dsp, f0)
        tol = RING_CARRIER_TOL_DB if (name == "ring" and lab == "carrier") else TONE_TOL_DB
        assert abs(dbv(a_ds) - dbv(a_sp)) < tol, (
            f"{lab} @ {f0:.0f} Hz: SPICE {dbv(a_sp):.2f} dBV vs DSP {dbv(a_ds):.2f} dBV"
        )


def test_mod_feedthrough_floor(balance_am_bin):
    """With the carrier off, both simulators leave only mod feedthrough.
    SPICE keeps the C302/Cgd coupling paths (~-77 dBV here); the DSP omits
    them (documented), so it is only asserted to sit below SPICE's floor."""
    over = dict(acar=0.0, amod=2.5)
    p = dict(DEFAULTS, **over)
    v_spice = run_tran(**over)
    v_dsp = dsp_two_tone(balance_am_bin, p)
    assert dbv(tone_amp(v_spice, 200.0)) < -70.0
    assert dbv(tone_amp(v_dsp, 200.0)) < -70.0


def test_dsp_depth_linearity(balance_am_bin):
    """DSP-side AM law: doubling the modulator level moves the first
    sidebands +6 dB and leaves the carrier (to first order)."""
    y1 = dsp_two_tone(balance_am_bin, dict(DEFAULTS, amod=0.25))
    y2 = dsp_two_tone(balance_am_bin, dict(DEFAULTS, amod=0.5))
    assert dbv(tone_amp(y2, 2200.0)) - dbv(tone_amp(y1, 2200.0)) == pytest.approx(6.02, abs=0.2)
    assert dbv(tone_amp(y2, 2000.0)) - dbv(tone_amp(y1, 2000.0)) == pytest.approx(0.0, abs=0.1)
