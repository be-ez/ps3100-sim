"""Derive the KLM-62 stage transfer function symbolically and discretize it.

Solves the two node equations of the C-input bandpass stage (series input
cap, both frequency-setting resistors = the two LDR elements of one P873D,
feedback cap inv->out;)
with sympy, checks the result against the closed-form used by the SPICE
tests, then bilinear-transforms the analog TF at fs=48 kHz over a log-spaced
Rldr grid (1k..1M) and writes the biquad coefficient interpolation table to
analysis/coeffs_table.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import sympy as sp
from scipy.signal import bilinear

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.ac_analysis import COLORS  # noqa: E402

FS = 48_000
N_TABLE = 64
RLDR_MIN, RLDR_MAX = 1e3, 1e6


def derive_symbolic() -> sp.Expr:
    """H(s) for the stage, from KCL at the input node and the virtual ground."""
    s, Vin, Vnp, Vout = sp.symbols("s Vin Vnp Vout")
    R, Ca, Cb = sp.symbols("R Ca Cb", positive=True)

    # input node np: Cin from Vin, LDR1 to out, LDR2 to virtual ground (0 V)
    kcl_np = sp.Eq((Vin - Vnp) * s * Ca, Vnp / R + (Vnp - Vout) / R)
    # virtual ground: current through LDR2 continues through Cfb to out
    kcl_vg = sp.Eq(Vnp / R, -Vout * s * Cb)

    sol = sp.solve([kcl_np, kcl_vg], [Vnp, Vout], dict=True)[0]
    return sp.simplify(sol[Vout] / Vin)


def analog_coeffs(ca: float, cb: float, rldr: float) -> tuple[list[float], list[float]]:
    """H(s) = -(s/(R*Cb)) / (s^2 + s*2/(R*Ca) + 1/(R^2*Ca*Cb))."""
    b1 = -1.0 / (rldr * cb)
    a1 = 2.0 / (rldr * ca)
    a0 = 1.0 / (rldr**2 * ca * cb)
    return [b1, 0.0], [1.0, a1, a0]


def check_derivation() -> None:
    H = derive_symbolic()
    s = sp.symbols("s")
    R, Ca, Cb = sp.symbols("R Ca Cb", positive=True)
    num, den = sp.fraction(sp.cancel(H))
    den = sp.Poly(den, s)
    num = sp.Poly(num, s)
    lead = den.coeffs()[0]
    a1 = sp.simplify(den.coeffs()[1] / lead)
    a0 = sp.simplify(den.coeffs()[2] / lead)
    b1 = sp.simplify(num.coeffs()[0] / lead)
    assert sp.simplify(a1 - 2 / (R * Ca)) == 0
    assert sp.simplify(a0 - 1 / (R**2 * Ca * Cb)) == 0
    assert sp.simplify(b1 - (-1 / (R * Cb))) == 0
    # derived invariants: Q = 0.5*sqrt(Ca/Cb), |H(f0)| = Ca/(2*Cb)
    w0 = sp.sqrt(a0)
    q = sp.simplify(w0 / a1)
    assert sp.simplify(q - sp.sqrt(Ca / Cb) / 2) == 0
    gain = sp.simplify(sp.Abs(b1) / a1)
    assert sp.simplify(gain - Ca / (2 * Cb)) == 0
    print("sympy derivation matches closed-form KLM-62 stage coefficients")
    print("  H(s) = -(s/(R*Cb)) / (s^2 + s*2/(R*Ca) + 1/(R^2*Ca*Cb))")
    print("  Q = sqrt(Ca/Cb)/2, |H(f0)| = Ca/(2*Cb)  (independent of R)")


def build_table(color: str = "yellow") -> dict:
    cin, cfb = COLORS[color]
    rldr_grid = np.geomspace(RLDR_MIN, RLDR_MAX, N_TABLE)
    rows = []
    for rldr in rldr_grid:
        num_a, den_a = analog_coeffs(cin, cfb, rldr)
        b, a = bilinear(num_a, den_a, fs=FS)
        rows.append({"rldr": float(rldr), "b": b.tolist(), "a": a.tolist()})
    return {
        "meta": {
            "fs": FS,
            "color": color,
            "rldr_min": RLDR_MIN,
            "rldr_max": RLDR_MAX,
            "note": "all three stages share one table; stagger is per-stage Rldr",
        },
        "stages": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO / "analysis" / "coeffs_table.json")
    ap.add_argument("--color", default="yellow", choices=COLORS)
    args = ap.parse_args()
    check_derivation()
    table = build_table(args.color)
    args.out.write_text(json.dumps(table, indent=1))
    print(f"wrote {args.out} ({N_TABLE} Rldr points)")


if __name__ == "__main__":
    main()
