"""Sanity gate for dsp/instrument.dsp, the composed voice chain.

No composite SPICE referee exists (there is no composite netlist); each board
is individually refereed by its own test module. This file guards the
composition itself: the chain compiles, the GEG->VCA wire actually gates the
voice, and the selected note survives the whole path to the output.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from tests.test_dsp_vs_spice import REPO, build_driver
from tests.test_siggen_dsp import FS, render

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None,
    reason="faust or c++ not installed",
)

N = 2 * FS  # 2 s: 0.1 s idle, gate 0.1..1.0 s, release tail


@pytest.fixture(scope="session")
def instrument_bin():
    return build_driver(REPO / "dsp" / "instrument.dsp", "instrument_ir")


@pytest.fixture(scope="session")
def cycle(instrument_bin):
    """One full gate cycle via the GEG's internal timer hooks, fast envelope
    pots, resonator wet, ensemble on, note A / octave row 1."""
    return render(
        instrument_bin,
        "gate_on=0.1",
        "gate_off=1.0",
        "delay=0",
        "attack=0",
        # traced GEG release sense is inverted: krel=1 is FAST
        "release=1",
        "note=4",
        "octave=1",
        n=N,
    )


def _rms(x):
    return float(np.sqrt(np.mean(x * x)))


def test_geg_gates_the_voice(cycle):
    idle = _rms(cycle[: int(0.08 * FS)])  # before gate_on
    sustain = _rms(cycle[int(0.5 * FS) : int(0.9 * FS)])
    tail = _rms(cycle[int(1.8 * FS) :])  # after release + vactrol decay
    assert sustain > 1e-3, "voice is silent during sustain"
    # Idle floor is ~1/69 of sustain (post 5V-retrim), not arbitrarily small:
    # the KLM-76 VCA's
    # VBE-compensating R304 tap (full-res scan re-read) keeps the LED at
    # ~14 uA at CV=0, and the shared P873 law clamps dark resistance at 1 M
    #.
    assert idle < sustain / 50, "voice leaks before the gate opens"
    assert tail < sustain / 30, "voice does not decay after release"


def test_note_survives_the_chain(cycle):
    """A4 selected at the siggen must dominate the output spectrum's
    fundamental region (the resonator/ensemble color the spectrum but do not
    move the fundamental)."""
    seg = cycle[int(0.5 * FS) : int(0.9 * FS)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), 1 / FS)
    band = (freqs > 100) & (freqs < 1000)
    peak = freqs[band][np.argmax(spec[band])]
    # note A, octave row 1: the re-read row-2 ladder makes the octave-up partial (A5 ~880 Hz)
    # the row's strongest - the selected pitch class must still carry it
    assert abs(peak - 880) < 20, f"strongest partial at {peak:.1f} Hz"


@pytest.fixture(scope="session")
def instrument_poly_bin():
    return build_driver(REPO / "dsp" / "instrument_poly.dsp", "instrument_poly_ir")


def test_poly_chord_sounds_and_silences(instrument_poly_bin):
    """C major (C3 E3 G3) keyed via the bitmask: the trigger conditioning must
    gate the GEG open and the chord must reach the output; no keys = silence
    (VCA dark floor only)."""
    n = 2 * FS
    # bits pc*4+oct, pc 0=F..7=C..11=E, oct row 2 for the C3 area
    chord = (1 << (7 * 4 + 2)) | (1 << (11 * 4 + 2)) | (1 << (2 * 4 + 2))
    lo, hi = chord & 0xFFFFFF, chord >> 24
    keyed = render(instrument_poly_bin, f"keys_lo={lo}", f"keys_hi={hi}", "nkeys=3", n=n)
    idle = render(instrument_poly_bin, "keys_lo=0", "keys_hi=0", "nkeys=0", n=n)
    rms_keyed = float(np.sqrt(np.mean(keyed[FS:] ** 2)))
    rms_idle = float(np.sqrt(np.mean(idle[FS:] ** 2)))
    assert rms_keyed > 1e-3, "keyed chord is silent"
    assert rms_idle < rms_keyed / 50, "idle output not near-silent"
