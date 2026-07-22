"""Structural gate for dsp/poly.dsp, the PS-3100 polyphonic voice core.

There is no 48-channel SPICE netlist to referee against. dsp/poly.dsp reuses
the SPICE-refereed shaper cell of dsp/siggen.dsp and the SPICE-refereed KORG35
core of dsp/gate.dsp verbatim, and re-derives only the master frequency law,
the octave-divider counter and the row ladder (which cannot be injected into
siggen's baked-in note/octave UI - see the poly.dsp header). So this module:

  1. pins the re-derivation sample-for-sample against dsp/siggen.dsp (master
     frequency + octave-row staircase levels), then
  2. tests the POLY STRUCTURE the validated pieces are wired into: phase-locked
     octave divider chains, independent pitch classes, additive 48-channel
     summing headroom, per-key gate isolation, and a chord render.

Test hooks (poly.dsp is a 0-input / 1-output synth):
  keys_lo (bits 0..23) / keys_hi (bits 24..47) : key bitmasks, bit = pc*4+oct,
      pc 0..11 in siggen note order (0=F .. 7=C .. 11=E), oct 0..3 (row k
      fundamental = fm/2^(k+1)). Two 24-bit masks keep every bit an exact
      integer in double.
  bypass_env    : gate instantly (no attack/release ramp) but still per-key.
  bypass_filter : output the raw post-VCA oscillator bus so the divider
                  structure is visible without the KORG35 coloring it.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from tests.test_dsp_vs_spice import REPO, build_driver
from tests.test_siggen_dsp import (
    FS,
    CT_PF,
    DVQ,
    IMIN,
    KDISQ,
    RR,
    VBQ,
    VT,
    render,
)

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None,
    reason="faust or c++ not installed",
)

N = 1 << 16  # ~1.37 s; enough cycles for the divider + fine spectral bins

# pitch-class indices in the siggen note order (0=F .. 11=E)
C, E, G, A = 7, 11, 2, 4


@pytest.fixture(scope="session")
def poly_bin():
    return build_driver(REPO / "dsp" / "poly.dsp", "poly_ir")


@pytest.fixture(scope="session")
def siggen_bin():
    return build_driver(REPO / "dsp" / "siggen.dsp", "siggen_ir")


# --- helpers ---------------------------------------------------------------


def keymask(voices) -> tuple[int, int]:
    """(keys_lo, keys_hi) for a list of (pc, oct) voices; bit = pc*4+oct."""
    lo = hi = 0
    for pc, oct_ in voices:
        b = pc * 4 + oct_
        if b < 24:
            lo |= 1 << b
        else:
            hi |= 1 << (b - 24)
    return lo, hi


def law_freq(pc: int, cv: float = -1.62) -> float:
    """Master frequency of pitch class pc at temperament bus cv, from the DSP's
    own closed form (same constants as dsp/siggen.dsp)."""
    ct = CT_PF[pc] * 1e-12
    i = max((VBQ - 0.545 - cv) / RR, IMIN)
    for _ in range(5):
        i = max((VBQ - VT * np.log(max(i, IMIN) / 1e-14) - cv) / RR, IMIN)
    return 1.0 / (DVQ * ct / i + KDISQ * ct)


def master_freq(x: np.ndarray, div: int, thr_frac: float = 0.3) -> float:
    """Master frequency from the once-per-staircase-cycle large downward step
    (same method as tests/test_siggen_dsp.py, with an amplitude-relative
    threshold since poly's bus is sig_trim-scaled). div = 2^(oct+1) counts per
    staircase cycle."""
    amp = x.max() - x.min()
    jumps = np.where(np.diff(x) < -thr_frac * amp)[0]
    return div / (np.diff(jumps).mean() / FS)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x * x)))


def peak_freqs(x, fmin=30.0, thresh_frac=0.05, max_peaks=16):
    """Spectral peak frequencies (Hann window, parabolic sub-bin refinement),
    strongest first."""
    x = x - x.mean()
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / FS)
    df = f[1] - f[0]
    top = X.max()
    out = []
    for i in range(2, len(X) - 1):
        if f[i] < fmin:
            continue
        if X[i] > X[i - 1] and X[i] >= X[i + 1] and X[i] > thresh_frac * top:
            a, b, c = (np.log(X[j] + 1e-30) for j in (i - 1, i, i + 1))
            denom = a - 2 * b + c
            d = 0.5 * (a - c) / denom if denom != 0 else 0.0
            out.append(((i + d) * df, X[i]))
    out.sort(key=lambda t: -t[1])
    return out[:max_peaks]


def harmonic_grid_dev(x, f0) -> float:
    """Max relative distance of the signal's spectral peaks from the nearest
    integer multiple of f0. ~0 iff every partial is a harmonic of f0."""
    freqs = [f for f, _ in peak_freqs(x)]
    dev = 0.0
    for f in freqs:
        k = round(f / f0)
        if k >= 1:
            dev = max(dev, abs(f / f0 - k) / k)
    return dev


# --- 1. parity: the re-derivation reproduces dsp/siggen.dsp -----------------


@pytest.mark.parametrize(
    ("pc", "oct_", "cv"),
    [(C, 2, -1.62), (A, 2, -1.62), (E, 3, -1.62), (A, 1, -3.0), (G, 0, -1.62)],
)
def test_master_frequency_matches_siggen(poly_bin, siggen_bin, pc, oct_, cv):
    """poly's re-derived master law + shared divider counter must oscillate at
    exactly the same frequency as dsp/siggen.dsp for the same note/octave/bus
    (guards the duplicated constants from drifting)."""
    lo, hi = keymask([(pc, oct_)])
    xp = render(
        poly_bin,
        f"keys_lo={lo}",
        f"keys_hi={hi}",
        "bypass_env=1",
        "bypass_filter=1",
        f"cv={cv}",
        n=N,
    )[FS // 2 :]
    xs = render(siggen_bin, f"note={pc}", f"octave={oct_}", f"cv={cv}", n=N)[FS // 2 :]
    div = 2 ** (oct_ + 1)
    fp, fs = master_freq(xp, div), master_freq(xs, div)
    assert fp == pytest.approx(fs, rel=5e-3), f"poly {fp:.2f} Hz vs siggen {fs:.2f} Hz"


def test_octave_row_levels_match_siggen(poly_bin, siggen_bin):
    """Each octave row's staircase (the re-read ladder pools reused via
    sigB.cell) must hit exactly the same set of output levels as siggen; poly's
    bus is only sig_trim-scaled (default 0.05)."""
    trim = 0.05
    for oct_ in range(4):
        lo, hi = keymask([(C, oct_)])
        xp = render(
            poly_bin,
            f"keys_lo={lo}",
            f"keys_hi={hi}",
            "bypass_env=1",
            "bypass_filter=1",
            n=N,
        )[FS // 2 :]
        xs = render(siggen_bin, f"note={C}", f"octave={oct_}", n=N)[FS // 2 :]
        up = np.unique(np.round(xp / trim, 2))
        us = np.unique(np.round(xs, 2))
        assert len(up) == len(us) and np.allclose(up, us, atol=3e-2), (
            f"oct{oct_}: poly/trim {up} vs siggen {us}"
        )


@pytest.mark.parametrize(
    ("wfd", "wfr", "mode"),
    [(0.0, 14.83, "saw"), (0.0, 10.5, "triangle"), (9.5, 0.4, "pulse")],
)
def test_shaper_table_matches_direct_cell(poly_bin, siggen_bin, wfd, wfr, mode):
    """poly.dsp does not run sigB.cell per sample: row k reads counter bits
    0..k, so it precomputes the cell at the 2^(k+1) possible ladder levels with
    compile-time bit patterns (Faust hoists those solves to control rate) and
    the audio loop selects among them. That is what makes 48 channels fit the
    browser's render quantum, so the table and its index must stay pinned to
    siggen's direct per-sample solve - including in the fold/comparator regions
    where the cell is most sensitive, and for the deepest row (16 levels, where
    a mis-indexed table is easiest to hide)."""
    trim = 0.05
    for oct_ in range(4):
        lo, hi = keymask([(C, oct_)])
        xp = (
            render(
                poly_bin,
                f"keys_lo={lo}",
                f"keys_hi={hi}",
                "bypass_env=1",
                "bypass_filter=1",
                f"wfd={wfd}",
                f"wfr={wfr}",
                n=N,
            )[FS // 2 :]
            / trim
        )
        xs = render(siggen_bin, f"note={C}", f"octave={oct_}", f"wfd={wfd}", f"wfr={wfr}", n=N)[
            FS // 2 :
        ]
        # same level SET (the table's values) ...
        up, us = np.unique(np.round(xp, 6)), np.unique(np.round(xs, 6))
        assert len(up) == len(us) and np.allclose(up, us, atol=1e-6), (
            f"{mode} oct{oct_}: levels {up} vs siggen {us}"
        )
        # ... and the same level at the same counter phase (the table's index):
        # both read the same divider, so the sequences must align sample-wise.
        assert np.allclose(xp, xs, atol=1e-6), (
            f"{mode} oct{oct_}: table index diverges from siggen's staircase "
            f"(max {np.max(np.abs(xp - xs)):.3g})"
        )


# --- 2. phase-locked octaves --------------------------------------------------


def test_phase_locked_octaves_are_harmonic(poly_bin):
    """Two octave rows of ONE pitch class (C oct2 + C oct1) share the master
    divider counter, so the sum is exactly periodic at the lower fundamental:
    every partial is a harmonic of f_low with NO beating sidebands. A control
    pair of two INDEPENDENT oscillators detuned by a realistic 0.3 % (the error
    real per-note VCOs would have, had the octaves not been hardware-locked)
    must fail the same test - proving the metric detects beating."""
    lo, hi = keymask([(C, 2), (C, 1)])
    x = render(
        poly_bin,
        f"keys_lo={lo}",
        f"keys_hi={hi}",
        "bypass_env=1",
        "bypass_filter=1",
        n=N,
    )[FS // 4 :]
    f_low = min(f for f, _ in peak_freqs(x))  # the C oct2 fundamental, fm/8

    locked = harmonic_grid_dev(x, f_low)
    assert locked < 1e-3, f"phase-locked partials off the harmonic grid by {locked:.2e}"

    # independent-oscillator control: a static period would be impossible, so
    # its octave line sits at 2*f_low*(1+eps) and breaks the harmonic grid
    t = np.arange(len(x)) / FS
    ctrl = np.sign(np.sin(2 * np.pi * f_low * t)) + np.sign(
        np.sin(2 * np.pi * 2 * f_low * 1.003 * t)
    )
    assert harmonic_grid_dev(ctrl, f_low) > 1e-3, "control should beat"
    assert harmonic_grid_dev(ctrl, f_low) > 5 * locked


def test_independent_pitch_classes(poly_bin):
    """Two DIFFERENT pitch classes (C + E) run off independent masters: both
    fundamentals are present at their own law frequency and neither is a
    harmonic of the other."""
    lo, hi = keymask([(C, 2), (E, 2)])
    x = render(
        poly_bin,
        f"keys_lo={lo}",
        f"keys_hi={hi}",
        "bypass_env=1",
        "bypass_filter=1",
        n=N,
    )[FS // 4 :]
    fc, fe = law_freq(C) / 8, law_freq(E) / 8  # oct2 fundamentals
    peaks = [f for f, _ in peak_freqs(x)]

    def present(f0):
        return any(abs(f - f0) / f0 < 0.01 for f in peaks)

    assert present(fc), f"C fundamental {fc:.1f} Hz missing from {peaks[:6]}"
    assert present(fe), f"E fundamental {fe:.1f} Hz missing from {peaks[:6]}"
    # they are genuinely independent (not an integer ratio -> no accidental lock)
    assert not (0.02 > abs((fe / fc) - round(fe / fc))), "C and E should not be harmonic"


# --- 3. summing headroom ------------------------------------------------------


def test_48_key_sum_is_additive_not_averaged(poly_bin):
    """The KLM-69 group mixer SUMS the channels (it does not average): the full
    48-key cluster is many times louder than one note, and stays finite. Guards
    against a divide-by-N summing bug."""
    lo1, hi1 = keymask([(C, 2)])
    one = render(poly_bin, f"keys_lo={lo1}", f"keys_hi={hi1}", n=FS)[FS // 2 :]
    allk = render(poly_bin, "keys_lo=16777215", "keys_hi=16777215", n=FS)[FS // 2 :]
    r1, r48 = rms(one), rms(allk)
    # additive, not averaged: >= a few x a single note (measured ~12x here;
    # between sqrt(48) for incoherent notes and 48x for coherent ones, since
    # each pitch class's 4 octaves are phase-coherent). NOT ~ r1/48.
    assert r48 > 3 * r1, f"48-key sum {r48:.4g} not additive over one note {r1:.4g}"
    assert r48 < 48 * r1, "48-key sum should not be fully coherent"
    assert np.all(np.isfinite(allk)) and np.abs(allk).max() < 1e3


# --- 4. gate isolation --------------------------------------------------------


def test_unkeyed_voices_are_silent(poly_bin):
    """Every one of the 48 oscillators runs continuously (no voice allocation),
    but an unkeyed channel's CD4007 VCA is fully off, so it contributes at or
    below the gate model's dark floor. With no key held the whole bus is silent
    (the DSP core has no feedthrough term; the hardware's residual is the CD4007
    ~0.5 pF stray, >50 dB down, not modeled - see docs)."""
    off = render(poly_bin, n=FS)[FS // 2 :]
    lo, hi = keymask([(C, 2)])
    one = render(poly_bin, f"keys_lo={lo}", f"keys_hi={hi}", n=FS)[FS // 2 :]
    assert rms(off) < 1e-6 * max(rms(one), 1e-9), "unkeyed bus is not silent"
    assert np.abs(off).max() < 1e-9


# --- 5. chord sanity ----------------------------------------------------------


def test_chord_render_sanity(poly_bin):
    """A C-major triad (C+E+G, octave row 2) through the full per-voice path
    (envelope, CD4007 VCA, KORG35): finite, audible, with spectral energy at
    each of the three note fundamentals."""
    lo, hi = keymask([(C, 2), (E, 2), (G, 2)])
    x = render(poly_bin, f"keys_lo={lo}", f"keys_hi={hi}", "attack=0.005", n=FS)[FS // 4 :]
    assert np.all(np.isfinite(x))
    assert rms(x) > 1e-3, "chord is silent"
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / FS)
    top = X.max()
    for pc in (C, E, G):
        f0 = law_freq(pc) / 8
        win = (f > f0 * 0.97) & (f < f0 * 1.03)
        assert win.any() and X[win].max() > 0.02 * top, (
            f"no energy at pc {pc} fundamental {f0:.1f} Hz"
        )
