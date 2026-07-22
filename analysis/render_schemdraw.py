"""Render a readable schematic of the KLM-62 resonator from the netlist data.

Component values (color caps) are parsed out of netlists/klm62-resonator.cir
so the drawing can never drift from the SPICE reference. One resonator
stage is drawn in full detail; the three instances are identical -- band
stagger comes from the per-stage vactrol LED drive (RESONATORS 2/2).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import schemdraw
import schemdraw.elements as elm

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm62-resonator.cir"


def netlist_params() -> dict[str, str]:
    text = NETLIST.read_text()
    m = re.search(r"^\.param\s+(.*)$", text, re.MULTILINE)
    return dict(kv.split("=") for kv in m.group(1).split())


def draw(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    p = netlist_params()
    with schemdraw.Drawing(file=str(out), show=False) as d:
        d.config(unit=2.5, fontsize=11)

        # --- input buffer IC11a: unity-gain Sallen-Key HPF, fc ~= 47.7 Hz ---
        d += elm.Dot(open=True).label("RES IN\n(16)", "left")
        d += (nin := elm.Dot())
        d += elm.Resistor().down().at(nin.start).label("R101\n100k", loc="bottom")
        d += elm.Ground()
        d += elm.Capacitor().right().at(nin.start).label("C101\n0.033u")
        d += (nx := elm.Dot())
        d += elm.Capacitor().right().label("C102\n0.033u")
        d += (np_ := elm.Dot())
        d += elm.Resistor().down().at(np_.start).label("R103\n68k", loc="bottom")
        d += elm.Ground()
        d += elm.Line().right().at(np_.start).length(0.8)
        d += (buf := elm.Opamp(leads=True).anchor("in2").label("IC11a", "center", ofst=(0, -1)))
        d += elm.Line().at(buf.out).right().length(0.5)
        d += (bout := elm.Dot())
        # follower feedback out -> inv input
        d += elm.Line().at(bout.start).up().length(1.2)
        d += elm.Line().left().tox(buf.in1)
        d += elm.Line().down().toy(buf.in1)
        # Sallen-Key feedback R102: nx -> out
        d += elm.Line().up().at(nx.start).length(2.6)
        d += elm.Resistor().right().tox(bout.start).label("R102\n150k")
        d += elm.Line().down().toy(bout.start)

        # --- R105/R106 pad ---
        d += elm.Resistor().right().at(bout.start).label("R105\n1k")
        d += (bus := elm.Dot())
        d += elm.Resistor().down().at(bus.start).label("R106\n27", loc="bottom")
        d += elm.Ground()

        # --- one resonator stage (x3, identical; LEDs staggered) ---
        d += elm.Capacitor().right().at(bus.start).label(f"C105-107\n{p['Cin']}")
        d += (sn := elm.Dot())
        # LDR2 to virtual ground
        d += elm.Resistor().right().at(sn.start).label("LDR2\n(P873D)")
        d += (ninv := elm.Dot())
        d += (
            op := elm.Opamp(leads=True).anchor("in1").label("IC11b/12a/12b", "center", ofst=(0, 1))
        )
        d += elm.Line().at(op.in2).down().length(0.7)
        d += elm.Ground()
        d += elm.Line().at(op.out).right().length(0.5)
        d += (so := elm.Dot())
        # feedback cap Cfb: ninv -> out
        d += elm.Line().up().at(ninv.start).length(1.8)
        d += elm.Capacitor().right().tox(so.start).label(f"C108-110\n{p['Cfb']}")
        d += elm.Line().down().toy(so.start)
        # LDR1: stage node -> out
        d += elm.Line().up().at(sn.start).length(3.4)
        d += elm.Resistor().right().tox(so.start).label("LDR1 (P873D)")
        d += elm.Line().down().toy(so.start)

        # --- summing amp IC13 ---
        d += elm.Resistor().right().at(so.start).label("R107-109\n10k")
        d += (nsum := elm.Dot())
        d += (sm := elm.Opamp(leads=True).anchor("in1").label("IC13", "center", ofst=(0, 1)))
        d += elm.Line().at(sm.in2).down().length(0.7)
        d += elm.Ground()
        d += elm.Line().up().at(nsum.start).length(1.8)
        d += elm.Resistor().right().tox(sm.out).label("R110\n10k")
        d += elm.Line().down().toy(sm.out)
        d += elm.Line().at(sm.out).right().length(0.5)
        d += elm.Resistor().right().label("R111\n100")
        d += elm.Capacitor().right().label("C111\n10u")
        d += elm.Dot(open=True).label("RES OUT1\n(20, wet)", "right")

        # annotations
        d += (
            elm.Label()
            .at((0, -6.5))
            .label(
                "Stage drawn once, instantiated 3x with IDENTICAL caps "
                f"(color variant: Cin={p['Cin']}, Cfb={p['Cfb']}); band stagger via per-stage "
                "vactrol LED drive (RESONATORS 2/2).\n"
                "Dry leg: buf_out -> R104 100R -> C103 10u -> RES OUT2 (pin 18); external ~1.5k "
                "blend pot between pins 20/18, wiper = OUT.",
                fontsize=9,
            )
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", type=Path, default=REPO / "schematics" / "klm62.svg")
    args = ap.parse_args()
    draw(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
