"""Validate the KLM-76 VCA / phone amp netlist against hand theory
:
audio-path transfer vs the exact complex-impedance derivation, C301 HPF
corner, the LED-driver CV law shape (dark idle, diode-chain expansion knee,
saturation cap), the unity-gain trim calibration, and the phone amp's
response and headroom."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm76-vca.cir"
VACTROL_LIB = REPO / "netlists" / "models" / "vactrol.lib"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# component values (scan page 0018; keep in sync with the netlist)
R301, R302 = 22e3, 3.3e3
C301 = 1e-6
CF302 = 47e-12
R309, R317 = 470.0, 220.0
KDIV = R302 / (R301 + R302)
RPRE = R301 * R302 / (R301 + R302)
VR_DEFAULT = 0.301  # netlist trim default: unity chain gain at vc=5 (panel max)
RF_DEFAULT = 22e3 + 100e3 * VR_DEFAULT

# ideal-opamp netlist vs exact linear theory: agreement is numerical only
THEORY_TOL_DB = 0.05
CV_STEP = 0.05  # dc sweep step (netlist .control)


def run_klm76(
    rldr1: float = 4.7e3,
    rldr2: float = 4.7e3,
    vr1: float = VR_DEFAULT,
    vr2: float = VR_DEFAULT,
    ngspice: str = "ngspice",
) -> dict[str, np.ndarray]:
    """One full deck run: CV dc sweeps, phone-amp dc transfer, audio AC."""
    deck = NETLIST.read_text()
    deck = re.sub(
        r"^\.param .*$",
        f".param vc1=5 vc2=5 rldr1={rldr1} rldr2={rldr2} vr1={vr1} vr2={vr2}",
        deck,
        count=1,
        flags=re.MULTILINE,
    )
    deck = deck.replace(".include models/vactrol.lib", f".include {VACTROL_LIB}")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            [ngspice, "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out = {}
        for name in ["vca_cv1", "vca_cv2", "vca_phdc", "vca_ac"]:
            f = tmpdir / f"{name}.txt"
            if not f.exists():
                raise RuntimeError(f"ngspice produced no {name}:\n{proc.stdout}\n{proc.stderr}")
            out[name] = np.loadtxt(f)
    # wrdata layout: [scale, var, scale, var, ...]
    cv1, cv2, ph, ac = out["vca_cv1"], out["vca_cv2"], out["vca_phdc"], out["vca_ac"]
    return {
        "cv": cv1[:, 0],
        "r1": cv1[:, 1],
        "iled1": cv1[:, 3],
        "r2": cv2[:, 1],
        "iled2": cv2[:, 3],
        "ph_in": ph[:, 0],
        "ph_out": ph[:, 1],
        "freq": ac[:, 0],
        "out38": ac[:, 1],
        "out35": ac[:, 3],
        "pho": ac[:, 5],
    }


def theory_chain_db(
    f: np.ndarray, rldr1: float, rldr2: float, rf1: float = RF_DEFAULT, rf2: float = RF_DEFAULT
) -> tuple[np.ndarray, np.ndarray]:
    """Exact linear transfer to out38 (VCA1 out, loaded by VCA2) and out35.

    Independent derivation from the schematic: each VCA is an inverting stage
    with input branch Thevenin (KDIV, RPRE), series Rldr + 1/(sC), feedback
    Rf (VCA2: Rf || CF302); VCA1 loads through R309 into VCA2's input
    impedance; out35 is unloaded (R317 carries no current).
    """
    w = 2j * np.pi * f
    zc = 1.0 / (w * C301)
    h1 = -KDIV * rf1 / (RPRE + rldr1 + zc)
    zin2 = R301 + 1.0 / (1.0 / R302 + 1.0 / (rldr2 + zc))
    kload = zin2 / (R309 + zin2)
    zf2 = rf2 / (1.0 + w * rf2 * CF302)
    h2 = -KDIV * zf2 / (RPRE + rldr2 + zc)
    v38 = h1 * kload
    return 20 * np.log10(np.abs(v38)), 20 * np.log10(np.abs(v38 * h2))


@pytest.fixture(scope="module")
def default_run():
    return run_klm76()


@pytest.mark.parametrize("rldr", [4.7e3, 22e3, 100e3, 470e3])
def test_audio_path_matches_theory(rldr):
    res = run_klm76(rldr1=rldr, rldr2=rldr)
    th38, th35 = theory_chain_db(res["freq"], rldr, rldr)
    assert np.abs(res["out38"] - th38).max() < THEORY_TOL_DB
    assert np.abs(res["out35"] - th35).max() < THEORY_TOL_DB


def test_hpf_corner(default_run):
    """C301 slides the HPF corner: fc = 1/(2*pi*C301*(RPRE+Rldr)) = 21 Hz at
    Rldr = 4.7k (the netlist default)."""
    res = default_run
    f, db = res["freq"], res["out38"]
    fc_theory = 1.0 / (2 * np.pi * C301 * (RPRE + 4.7e3))
    mid = np.interp(1e3, f, db)
    low = f < 200.0
    # db rises with f below 200 Hz -> interpolate the -3 dB crossing
    fc = np.interp(mid - 3.0103, db[low], f[low])
    assert fc == pytest.approx(fc_theory, rel=0.05)


def test_led_drive_law_shape(default_run):
    """DC law of the Q301 driver with the corrected R304 tap (node above
    D301, full-res re-read 2026-07-21): the base rides one D301 Vf above the
    CV, a VBE-compensating shift that moves the whole law ~0.6 V left of the
    old CV-node reading - the old ~0.55 V turn-on lands at ~-0.05 V, off the
    panel range. Landmarks over the REAL 0..6 V sweep (panel max +5 V, GEG
    sustain +5.87 V - reconciliation 2026-07-21): dark idle at CV=0,
    monotone rise, diode-chain expansion knee near 2.6 V, and NO saturation
    plateau in range (Q301's ~8 V saturation against the LED + R307 drop is
    unreachable from any real panel source)."""
    res = default_run
    i, r = res["iled1"], res["r1"]

    def j(v):
        return int(round(v / CV_STEP))

    # dark idle: at CV=0 node A sits one diode drop (~0.55 V) above G1,
    # right at Q301's Vbe turn-on -> the LED idles at ~14 uA (was hard-off
    # below 0.55 V with the old tap) and the LDR is dark but no longer
    # clamped at the model's 1 MOhm limit (~470k)
    assert 1e-6 < i[j(0.0)] < 5e-5
    assert 3e5 < r[j(0.0)] < 1e6
    # monotone nondecreasing across the whole real range
    assert np.all(np.diff(i) > -1e-9)
    # expansion knee: with the D301 Vf cancelling Q301's Vbe, Ve ~= CV, so
    # the 4-diode chain's Ve ~= 2.5 V knee sits at CV ~= 2.6 V (was ~3.2 V);
    # 1 V windows straddling it
    slope_lo = i[j(1.9)] - i[j(0.9)]
    slope_hi = i[j(4.4)] - i[j(3.4)]
    assert slope_hi > 3 * slope_lo
    # panel-max drive: ~5.3 mA at +5 V, ~7.5 mA at the 6 V sweep top - well
    # below the ~11.6 mA saturation cap and still climbing (>20 % rise from
    # 5 to 6 V), i.e. the driver never saturates on the real CV range
    assert 4e-3 < i[j(5.0)] < 7e-3
    assert 6e-3 < i[-1] < 9e-3
    assert i[-1] > 1.2 * i[j(5.0)]
    # bright resistance lands in the P873 power-law bright range: ~3.9k at
    # the +5 V panel max, ~3.0k at the 6 V sweep top
    assert 3e3 < r[j(5.0)] < 5e3
    assert 2e3 < r[-1] < 4e3


def test_vca2_driver_mirrors_vca1(default_run):
    res = default_run
    assert np.allclose(res["r1"], res["r2"], rtol=1e-6)
    assert np.allclose(res["iled1"], res["iled2"], rtol=1e-6)


def test_unity_gain_trim_at_full_drive(default_run):
    """The vr=0.301 trim default is calibrated so the chain is unity at the
    REAL full drive (+5 V, the panel jacks' printed max - reconciliation
    2026-07-21; was vr=0.162 at the falsified 10 V point): run the audio
    path at Rldr(vc=5) from the dc sweep. The only residual is the R309/Zin2
    inter-stage loading (~ -0.17 dB)."""
    r_full = default_run["r1"][int(round(5.0 / CV_STEP))]
    res = run_klm76(rldr1=r_full, rldr2=r_full)
    mid = np.interp(1e3, res["freq"], res["out35"])
    assert abs(mid) < 0.3


def test_geg_sustain_overdrive(default_run):
    """Documented residual of calibrating at the +5 V panel max instead of
    the traced GEG OUT2 sustain: at +5.87 V (Rldr ~3.1k) the chain sits
    ~+2.1 dB above unity - a mild overdrive, nowhere near the driver's
    (unreachable) saturation plateau."""
    r_geg = np.interp(5.87, default_run["cv"], default_run["r1"])
    res = run_klm76(rldr1=r_geg, rldr2=r_geg)
    mid = np.interp(1e3, res["freq"], res["out35"])
    assert 1.7 < mid < 2.5


def test_phone_amp_flat_and_headroom(default_run):
    """Non-inverting amp (full-res scan re-read 2026-07-21): R331 10k into
    the + input, R332 10k on the inverting node with R333 27k overall
    feedback -> gain 1 + 27/10 = 3.7 (+11.36 dB), flat across the audio
    band; the DC transfer is linear at slope 3.7 and clips against the 4558
    swing +/- the follower's Vbe (asymmetric: the NPN side clips one Vbe
    below the opamp swing, the PNP side one Vbe above the negative swing's
    diode-shifted base)."""
    res = default_run
    assert np.abs(res["pho"] - 20 * np.log10(3.7)).max() < 0.05
    vin, vout = res["ph_in"], res["ph_out"]
    lin = np.abs(vin) <= 3.0
    assert np.abs(vout[lin] - 3.7 * vin[lin]).max() < 0.1
    assert 12.0 < vout.max() < 14.5
    assert 12.0 < -vout.min() < 14.5
