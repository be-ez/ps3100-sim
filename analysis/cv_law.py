"""Extract the KLM-62D CV -> Rldr drive law from SPICE and fit a closed form.

Runs ngspice DC sweeps of netlists/klm62-cv.cir (the RESONATORS 2/2 LED
driver: servo-referenced exponential converter, 270k CV matrix, 470R-capped
LED drive, P873-class photocell power law), extracting per-band
Rldr(cv, peak_i) over the panel range, plus the LED currents.

Because the matrix inputs sum linearly into one exponential, the law is
separable: over the unclamped range

    log2(Rldr_i) = b_i + a * cv + s * vpk_i(peak_i)

where a = d(log2 R)/d(cv) is the master-sweep slope, s = d(log2 R)/dV is the
per-volt slope at any matrix input, vpk_i is the loaded peak-pot wiper
voltage, and b_i are per-band offsets set by the FC trim defaults (calibrated
to the factory-intent octave stagger).  f0 is proportional to 1/Rldr, so
log2(f0_i) is affine in cv with per-band offsets -- the closed form the DSP
consumes.

Operating point: the RES MOD 1/2 buses are the panel PEAK FREQ CV jack, printed -5V~+5V
(p0023), and the canonical sweep source (MG2 OUT) delivers a +/-2.73 V
triangle.  The bus is therefore BIPOLAR:
cv in [0,1] maps to vrm1 = VRM_SPAN*(cv - 0.5) = -5..+5 V, with cv = 0.5
<=> 0 V bus = nothing patched (jack normalled to ground) = no modulation,
where the FC trims anchor the factory center frequencies.

Writes analysis/cv_law.json: the raw grid, the fit coefficients, the fit
error, and the LED-current volts/octave (hardware figure ~1.8 V/oct).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm62-cv.cir"

BANDS = ["A", "B", "C"]  # = audio stages 1/2/3 (F101/F102/F103), low to high

# cv in [0,1] -> RES MOD 1 bus voltage: vrm = VRM_SPAN*(cv - 0.5), i.e. the
# panel PEAK FREQ CV jack's -5..+5 V bipolar range (p0023); cv=0.5 <=> 0 V
# bus = no modulation
VRM_SPAN = 10.0
VRM_MIN, VRM_MAX = -VRM_SPAN / 2.0, VRM_SPAN / 2.0


def cv_to_vrm(cv: float) -> float:
    return VRM_SPAN * (cv - 0.5)


# FC trimmer default positions; keep in sync with the netlist .param line.
# Calibrated so that at cv=0.5 (vrm1 = 0 V, no modulation), peaks at 0.5,
# Rldr = 47k / 23.5k / 11.75k -- the audio netlist's provisional
# octave-stagger defaults (factory FC trim intent: adjacent bands one octave
# apart).
FC_DEFAULTS = (0.4269, 0.2510, 0.0794)

# peak-pot positions sampled for the grid (panel range)
PEAK_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]

# clamp guard band: the photocell power law in the netlist clamps at
# [1k, 1M]; fits only use points safely inside it
R_CLAMP_LO, R_CLAMP_HI = 1e3, 1e6
R_FIT_LO, R_FIT_HI = 1.3e3, 0.75e6


def loaded_peak_voltage(p: float) -> float:
    """Loaded wiper voltage of a peak pot at position p in [0,1].

    10k pot from +10V to ground; wiper drives the 10k on-board pin load
    (R135-R137) in parallel with the 270k matrix resistor (whose far end
    sits at the mV-level CV node, i.e. effectively ground).
    """
    rp, rload = 10e3, 1.0 / (1.0 / 10e3 + 1.0 / 270e3)
    rtop, rbot = rp * (1.0 - p), rp * p
    zb = 1.0 / (1.0 / rbot + 1.0 / rload) if p > 0 else 0.0
    return 10.0 * zb / (rtop + zb)


def run_dc(
    pk: tuple[float, float, float] = (0.5, 0.5, 0.5),
    fc: tuple[float, float, float] = FC_DEFAULTS,
    vrm2: float = 0.0,
    step: float = 0.1,
    ngspice: str = "ngspice",
) -> dict[str, np.ndarray]:
    """One DC sweep of RES MOD 1 over the panel bus range VRM_MIN..VRM_MAX.

    Returns {'cv', 'r' (3, n) ohms, 'iled' (3, n) amps}.
    """
    deck = NETLIST.read_text()
    deck = re.sub(
        r"^\.param .*$",
        ".param vrm1=0 vrm2={} pkA={} pkB={} pkC={} fcA={} fcB={} fcC={}".format(vrm2, *pk, *fc),
        deck,
        count=1,
        flags=re.MULTILINE,
    )
    deck = deck.replace("dc VRM1 -5 5 0.05", f"dc VRM1 {VRM_MIN:g} {VRM_MAX:g} {step:g}")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            [ngspice, "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out_file = tmpdir / "cv_out.txt"
        if not out_file.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        data = np.loadtxt(out_file)
    # wrdata layout: (sweep, value) pairs per variable, order rA rB rC iA iB iC
    return {
        "cv": data[:, 0] / VRM_SPAN + 0.5,
        "r": data[:, [1, 3, 5]].T,
        "iled": data[:, [7, 9, 11]].T,
    }


def build_grid(ngspice: str = "ngspice") -> dict:
    """Rldr(cv, peak) grid: one master sweep per peak-pot setting.

    All three pots move together per run; each band only responds to its own
    pot (the matrix has no cross-band path), verified by test_cv_law.py.
    """
    grid = {}
    for p in PEAK_GRID:
        res = run_dc(pk=(p, p, p), ngspice=ngspice)
        grid[f"{p:g}"] = res
    return grid


def _affine_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Least-squares y = a*x + b; returns (a, b, rms residual)."""
    a, b = np.polyfit(x, y, 1)
    rms = float(np.sqrt(np.mean((np.polyval([a, b], x) - y) ** 2)))
    return float(a), float(b), rms


def fit_law(grid: dict) -> dict:
    """Fit the closed form and the LED-current exponential law."""
    fit: dict = {"bands": []}

    # per-band: log2(R) affine in cv at default peaks, over the unclamped range
    mid = grid["0.5"]
    for i, band in enumerate(BANDS):
        cv, r, iled = mid["cv"], mid["r"][i], mid["iled"][i]
        ok = (r > R_FIT_LO) & (r < R_FIT_HI)
        a, b, rms = _affine_fit(cv[ok], np.log2(r[ok]))
        # LED-current law: ln(I) vs bus volts -> hardware volts/octave figure
        si, sb, srms = _affine_fit((cv[ok] - 0.5) * VRM_SPAN, np.log(iled[ok]))
        fit["bands"].append(
            {
                "band": band,
                "oct_per_cv": a,  # d(log2 R)/d(cv); f0 slope is -a
                "log2_r_at_cv0": b,
                "rms_err_oct": rms,
                "volts_per_octave_current": np.log(2.0) / si,
                "iled_fit_rms": srms,
            }
        )

    # per-volt matrix-input slope from the peak sweeps: log2(R) vs loaded
    # wiper voltage at fixed cv=0.5 (read off the cv grid midpoint)
    pk_pts = []
    for pstr, res in grid.items():
        p = float(pstr)
        j = int(np.argmin(np.abs(res["cv"] - 0.5)))
        for i in range(3):
            r = res["r"][i][j]
            if R_FIT_LO < r < R_FIT_HI:
                pk_pts.append((loaded_peak_voltage(p), np.log2(r), i))
    pk_pts_arr = np.array([(v, lr) for v, lr, _ in pk_pts])
    _, _, s_rms = _affine_fit(pk_pts_arr[:, 0], pk_pts_arr[:, 1])
    # remove band-offset variance: fit slope per band then average
    slopes = []
    for i in range(3):
        pts = np.array([(v, lr) for v, lr, k in pk_pts if k == i])
        if len(pts) >= 3:
            si_, _, _ = _affine_fit(pts[:, 0], pts[:, 1])
            slopes.append(si_)
    fit["oct_per_volt"] = float(np.mean(slopes))  # d(log2 R)/dV at any input
    fit["oct_per_volt_rms"] = s_rms

    # separability check: master slope should equal oct_per_volt * VRM_SPAN
    fit["separability_err_oct_per_cv"] = float(
        np.mean([b["oct_per_cv"] for b in fit["bands"]]) - fit["oct_per_volt"] * VRM_SPAN
    )
    return fit


def build_law(ngspice: str = "ngspice") -> dict:
    grid = build_grid(ngspice=ngspice)
    fit = fit_law(grid)
    return {
        "meta": {
            "netlist": str(NETLIST.relative_to(REPO)),
            "cv_to_vrm1": "vrm1 = {:g}*(cv - 0.5), panel PEAK FREQ CV jack {:g}..{:g} V".format(
                VRM_SPAN, VRM_MIN, VRM_MAX
            ),
            "vrm_span": VRM_SPAN,
            "fc_defaults": list(FC_DEFAULTS),
            "peak_grid": PEAK_GRID,
            "r_clamp": [R_CLAMP_LO, R_CLAMP_HI],
            "photocell": "R = 15k * (I_led/1mA)^-0.8, clamped [1k, 1M] "
            "(P873-class CdS power law; see netlist header for provenance)",
        },
        "grid": {
            pstr: {
                "cv": res["cv"].tolist(),
                "rldr": [res["r"][i].tolist() for i in range(3)],
                "iled": [res["iled"][i].tolist() for i in range(3)],
            }
            for pstr, res in grid.items()
        },
        "fit": fit,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO / "analysis" / "cv_law.json")
    ap.add_argument("--ngspice", default="ngspice")
    args = ap.parse_args()

    if shutil.which(args.ngspice) is None:
        raise SystemExit("ngspice not found on PATH")

    law = build_law(ngspice=args.ngspice)
    args.out.write_text(json.dumps(law, indent=1))
    print(f"wrote {args.out}")
    fit = law["fit"]
    for b in fit["bands"]:
        print(
            f"  band {b['band']}: {-b['oct_per_cv']:+.2f} oct(f0)/cv, "
            f"R(cv=0) = {2 ** b['log2_r_at_cv0'] / 1e3:.1f}k, "
            f"fit rms {b['rms_err_oct'] * 100:.1f} centi-oct, "
            f"{b['volts_per_octave_current']:.2f} V/oct (LED current)"
        )
    print(
        f"  matrix input slope: {-fit['oct_per_volt']:.3f} oct(f0)/V, "
        f"separability err {fit['separability_err_oct_per_cv']:.4f} oct/cv"
    )


if __name__ == "__main__":
    main()
