"""Compare the KLM-76 Faust model (dsp/vca.dsp) against the SPICE reference
(netlists/klm76-vca.cir): frequency response of the VCA1->VCA2 chain at fixed
Rldr and at fixed CV points (through the DSP's static CV law), the CV->Rldr
table's provenance, the phone amp, and a vactrol-dynamics sanity check."""

import re
import shutil

import numpy as np
import pytest

from tests.test_dsp_vs_spice import REPO, build_driver, dsp_response_db, render
from tests.test_vca_spice import CV_STEP, run_klm76

VCA_DSP = REPO / "dsp" / "vca.dsp"

FMIN, FMAX = 30.0, 20_000.0
# below 10 kHz the DSP omits CF302 (47p across VCA2's feedback), whose corner
# dropped to ~65 kHz with the 2026-07-21 retrim (Rf 38.2k -> 52.1k, unity at
# the real +5 V max): a physical -0.10 dB at 10 kHz on top of numerical
# residue of the bilinear one-pole HPF
MID_TOL_DB = 0.15
# above 10 kHz the omitted CF302 costs up to a physical -0.39 dB at 20 kHz
# at the default trim (see dsp/vca.dsp header) -- not a loosened claim about
# the modeled band
HI_TOL_DB = 0.5
HI_HZ = 10_000.0
# fixed-CV comparisons add the 41-point log2(R) table interpolation error
# (<= 0.10 dB per stage for CV >= 0.3 V, doubled across the two VCAs)
CV_MID_TOL_DB = 0.4
CV_HI_TOL_DB = 0.65

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)


@pytest.fixture(scope="session")
def vca_bin():
    return build_driver(VCA_DSP, "vca_ir")


def spice_chain_db(rldr: float) -> tuple[np.ndarray, np.ndarray]:
    res = run_klm76(rldr1=rldr, rldr2=rldr)
    return res["freq"], res["out35"]


def compare_chain(vca_bin, rldr, dsp_args, mid_tol, hi_tol):
    ir = render(vca_bin, "monitor=0", *dsp_args)
    dsp_f, dsp_db = dsp_response_db(ir)
    f, spice_db = spice_chain_db(rldr)
    mask = (f >= FMIN) & (f <= FMAX)
    f = f[mask]
    spice_db = spice_db[mask]
    dsp_on_f = np.interp(np.log10(f), np.log10(dsp_f[1:]), dsp_db[1:])
    err = np.abs(dsp_on_f - spice_db)
    mid = f < HI_HZ
    assert err[mid].max() < mid_tol, (
        f"midband error {err[mid].max():.3f} dB at {f[mid][np.argmax(err[mid])]:.0f} Hz"
    )
    assert err[~mid].max() < hi_tol, (
        f"HF error {err[~mid].max():.3f} dB at {f[~mid][np.argmax(err[~mid])]:.0f} Hz"
    )


@pytest.mark.parametrize("rldr", [4.7e3, 22e3, 100e3, 470e3])
def test_dsp_matches_spice_fixed_rldr(vca_bin, rldr):
    """Audio path referee at forced LDR values (CV law bypassed)."""
    compare_chain(vca_bin, rldr, [f"rldr1={rldr}", f"rldr2={rldr}"], MID_TOL_DB, HI_TOL_DB)


@pytest.mark.parametrize("cv", [0.15, 0.3, 0.5, 0.7, 0.85, 1.0])
def test_dsp_matches_spice_at_cv_points(vca_bin, cv):
    """End-to-end static referee: the DSP's CV->Rldr table law (dynamics
    bypassed) against SPICE run at the LDR value its own LED driver settles
    to at the same CV. cv is the 0..1 control = pin volts / 5 (the real
    0V~+5V panel jack scale, reconciliation 2026-07-21); points below 0.06
    (0.3 V) sit in the dark-idle corner (steep table-interp region with the
    corrected VBE-compensated tap) where both layers are at < -70 dB chain
    gain."""
    res = run_klm76()
    j = int(round(5.0 * cv / CV_STEP))
    r_spice = res["r1"][j]
    compare_chain(
        vca_bin,
        r_spice,
        ["bypass_vactrol=1", f"cv1={cv}", f"cv2={cv}"],
        CV_MID_TOL_DB,
        CV_HI_TOL_DB,
    )


def test_cv_table_matches_netlist():
    """The 41-point log2(Rldr) table hardcoded in dsp/vca.dsp is sampled at
    0.15 V steps from the netlist's 0..6 V dc sweep (the real CV range:
    panel max +5 V, GEG sustain +5.87 V); regenerate it if the netlist
    changes. Tolerance is solver noise only."""
    src = VCA_DSP.read_text()
    m = re.search(r"cvTable = \(([^;]*)\);", src)
    assert m, "cvTable not found in dsp/vca.dsp"
    table = np.array([float(x) for x in m.group(1).replace("\n", " ").split(",")])
    assert len(table) == 41
    res = run_klm76()
    stride = int(round(0.15 / CV_STEP))
    fresh = np.log2(res["r1"][::stride])
    assert len(fresh) == 41
    assert np.abs(table - fresh).max() < 0.02


def test_phone_amp_gain(vca_bin):
    """Non-inverting amp, gain 1 + R333/R332 = 3.7: flat +11.36 dB
    (headroom clip is far above the impulse level)."""
    ir = render(vca_bin, "monitor=1")
    f, db = dsp_response_db(ir)
    band = (f >= FMIN) & (f <= FMAX)
    assert np.abs(db[band] - 20 * np.log10(3.7)).max() < 0.01


def test_vactrol_dynamics_power_up_dark(vca_bin):
    """With dynamics active the vactrols power up dark (rmax), so an impulse
    at t=0 sees the ~-80 dB dark chain even at full CV; bypassing the
    dynamics at the same CV sees the settled bright gain. Confirms the
    reused dsp/vactrol.dsp lag is actually in the signal path."""
    n = 1 << 14
    dyn = render(vca_bin, "monitor=0", "cv1=1.0", "cv2=1.0", n=n)
    stat = render(vca_bin, "monitor=0", "cv1=1.0", "cv2=1.0", "bypass_vactrol=1", n=n)
    assert np.all(np.isfinite(dyn)) and np.all(np.isfinite(stat))
    assert np.abs(stat).max() > 100.0 * np.abs(dyn).max()
