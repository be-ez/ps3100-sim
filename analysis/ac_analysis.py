"""Run ngspice AC analysis on the KLM-62 netlist and extract band metrics.

Generates variant decks by substituting the .param line of the reference
netlist (color cap table, per-stage Rldr, blend k), runs `ngspice -b`, parses
the wrdata output, finds each stage's bandpass peak and Q, and writes
analysis/reference.json -- the golden reference the DSP layer is
regression-tested against.
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
NETLIST = REPO / "netlists" / "klm62-resonator.cir"
VACTROL_LIB = REPO / "netlists" / "models" / "vactrol.lib"

# color variant cap table (KLM-62 scan; same caps for all three stages)
COLORS: dict[str, tuple[float, float]] = {
    "yellow": (0.082e-6, 820e-12),
    "green": (0.068e-6, 680e-12),
    "blue": (0.056e-6, 560e-12),
    "gray": (0.039e-6, 390e-12),
    "white": (0.033e-6, 330e-12),
}

# LDR sweep grid, log-spaced over the P873D useful range
RLDR_GRID = [4.7e3, 22e3, 100e3, 470e3, 1e6]

# provisional band stagger: Rldr ratios per stage (1, 1/2, 1/4 -> octaves,
# f0 proportional to 1/R). The real stagger is set by the RESONATORS 2/2
# LED drive circuit; revisit once that sheet's transfer is modeled.
STAGE_R_SCALE = [1.0, 0.5, 0.25]

TRACES = ["out", "o1", "o2", "o3", "sum"]


def stage_f0(cin: float, cfb: float, rldr: float) -> float:
    """f0 of one stage: 1/(2*pi*R*sqrt(Cin*Cfb)); f0 is proportional to 1/R."""
    return 1.0 / (2 * np.pi * rldr * np.sqrt(cin * cfb))


def stage_q(cin: float, cfb: float) -> float:
    """Constant over the sweep: 0.5*sqrt(Cin/Cfb) (~5 for all colors)."""
    return 0.5 * np.sqrt(cin / cfb)


def stage_gain_db(cin: float, cfb: float) -> float:
    """Mid-band gain Cin/(2*Cfb) (~50, +34 dB), constant over the sweep."""
    return 20 * np.log10(cin / (2 * cfb))


def run_ac(
    cin: float,
    cfb: float,
    rldr: tuple[float, float, float],
    k: float = 1.0,
    ngspice: str = "ngspice",
) -> dict[str, np.ndarray]:
    """One AC sweep; returns {'freq', 'out', 'o1', 'o2', 'o3'} (dB)."""
    deck = NETLIST.read_text()
    deck = re.sub(
        r"^\.param .*$",
        f".param Cin={cin} Cfb={cfb} Rldr1={rldr[0]} Rldr2={rldr[1]} Rldr3={rldr[2]} k={k}",
        deck,
        count=1,
        flags=re.MULTILINE,
    )
    deck = deck.replace(".include models/vactrol.lib", f".include {VACTROL_LIB}")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            [ngspice, "-b", "deck.cir"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )
        out_file = tmpdir / "ac_out.txt"
        if not out_file.exists():
            raise RuntimeError(f"ngspice produced no output:\n{proc.stdout}\n{proc.stderr}")
        data = np.loadtxt(out_file)
    result = {"freq": data[:, 0]}
    for i, name in enumerate(TRACES):
        result[name] = data[:, 2 * i + 1]
    return result


def staggered(rldr: float) -> tuple[float, float, float]:
    """Per-stage Rldr triple for a base Rldr using the provisional stagger."""
    return tuple(rldr * s for s in STAGE_R_SCALE)


def peak_metrics(freq: np.ndarray, db: np.ndarray) -> dict[str, float]:
    """Band center (parabolic interpolation on log f), peak level, -3 dB Q."""
    i = int(np.argmax(db))
    logf = np.log10(freq)
    if 0 < i < len(db) - 1:
        y0, y1, y2 = db[i - 1], db[i], db[i + 1]
        denom = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        f0 = 10 ** (logf[i] + delta * (logf[i + 1] - logf[i]))
        peak_db = y1 - 0.25 * (y0 - y2) * delta
    else:
        f0, peak_db = freq[i], db[i]

    half = peak_db - 3.0103
    lo = hi = None
    for j in range(i, 0, -1):
        if db[j - 1] <= half:
            lo = np.interp(half, [db[j - 1], db[j]], [freq[j - 1], freq[j]])
            break
    for j in range(i, len(db) - 1):
        if db[j + 1] <= half:
            hi = np.interp(half, [db[j + 1], db[j]], [freq[j + 1], freq[j]])
            break
    q = f0 / (hi - lo) if lo is not None and hi is not None else float("nan")
    return {"f0": float(f0), "peak_db": float(peak_db), "q": float(q)}


def build_reference(ngspice: str = "ngspice") -> dict:
    """Golden reference: band metrics for all colors, full responses for yellow.

    Sweeps the base Rldr with the provisional octave stagger, full wet (k=1).
    """
    ref: dict = {
        "meta": {
            "netlist": str(NETLIST.relative_to(REPO)),
            "stage_r_scale": STAGE_R_SCALE,
            "rldr_grid": RLDR_GRID,
            "blend_k": 1.0,
        },
        "colors": {},
    }
    for color, (cin, cfb) in COLORS.items():
        entries = {}
        for rldr in RLDR_GRID:
            res = run_ac(cin, cfb, staggered(rldr), k=1.0, ngspice=ngspice)
            entry: dict = {
                "bands": [peak_metrics(res["freq"], res[f"o{n + 1}"]) for n in range(3)],
                "theory_f0": [stage_f0(cin, cfb, r) for r in staggered(rldr)],
                "theory_q": stage_q(cin, cfb),
            }
            if color == "yellow":
                entry["response"] = {
                    "freq": res["freq"].tolist(),
                    "out_db": res["out"].tolist(),
                }
            entries[f"{rldr:g}"] = entry
        ref["colors"][color] = entries
    return ref


def plot_bode(ref: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5))
    for rldr, entry in ref["colors"]["yellow"].items():
        r = entry["response"]
        ax.semilogx(r["freq"], r["out_db"], label=f"base Rldr = {rldr} Ω")
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("|H| (dB)")
    ax.set_title("KLM-62 resonator, yellow variant, wet output vs base Rldr (octave stagger)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO / "analysis" / "reference.json")
    ap.add_argument("--plot", type=Path, default=None)
    ap.add_argument("--ngspice", default="ngspice")
    args = ap.parse_args()

    if shutil.which(args.ngspice) is None:
        raise SystemExit("ngspice not found on PATH")

    ref = build_reference(ngspice=args.ngspice)
    args.out.write_text(json.dumps(ref, indent=1))
    print(f"wrote {args.out}")
    for color, entries in ref["colors"].items():
        entry = entries["22000"]
        f0s = ", ".join(f"{b['f0']:.0f} Hz (Q {b['q']:.2f})" for b in entry["bands"])
        print(f"  {color:>6} @ base Rldr=22k: {f0s}")
    if args.plot:
        plot_bode(ref, args.plot)
        print(f"wrote {args.plot}")


if __name__ == "__main__":
    main()
