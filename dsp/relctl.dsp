// KLM-62D RELEASE CONTROL (PS-3100/3300), real-time model.
//
// Mirrors netlists/klm62d-relctl.cir.
//
// One utility board per instrument: two panel switch lines ("GND ON" -
// engaging the switch grounds the pin) set the shared GATE RELEASE
// TERMINAL (pin 5) level consumed by the gate boards:
//
//   RELEASE grounded (any HD) -> +11.610 V   full release (R14/R15 divider)
//   HD grounded, RELEASE open -> +5.8..8.0 V half damp (Q2 sat through
//                                            R13 + VR1 "HALF D ADJ")
//   both open (idle)          -> +0.14 V     damped (Q1 saturated)
//
// C1 (1 uF to the stiff +14.9 V rail = AC ground at the HD node) slews
// every Q1 (full-damp) state change through the 100k lattice: leaving or
// entering the damped state takes ~40..60 ms (declick). The Q2 (half-damp)
// path has no capacitor: its edges are microsecond-fast (one sample here).
//
// The model is the piecewise-linear nodal solve validated against ngspice
// in tests/test_relctl_spice.py (levels <0.05 V, transition times <7 %)
// and refereed directly against SPICE transients in tests/test_relctl_dsp.py.
// C1's node is the one state variable, integrated forward-Euler at SR
// (dt/tau ~ 5e-4 at 48 kHz: stable, sub-percent pole error).
//
// NOTE on startup: the recursion state starts at B = 0 V (the HD-grounded
// condition). With other switch settings the output settles with the
// board's own ~60 ms time constant - physical behavior, but allow ~0.4 s
// of pre-roll before measuring (the offline tests do).
//
// Controls: release, hd (panel switches, 0/1), halfd (VR1 trim 0..1,
// factory-set half-damp level +5.8..+8.0 V, 1 = full 4.7k = +7.97 V).
// Output: the terminal voltage in volts (control signal, not audio).
import("stdfaust.lib");

relsw = hslider("release", 0, 0, 1, 1) > 0.5; // 1 = pin 6 grounded
hdsw = hslider("hd", 0, 0, 1, 1) > 0.5;       // 1 = pin 4 grounded
halfd = hslider("halfd", 1.0, 0.0, 1.0, 0.001);

// netlist constants (ohms, farads, volts)
VCC = 14.9;
R7 = 100e3; R8 = 100e3; R9 = 100e3; R10 = 100e3; R11 = 100e3; R12 = 100e3;
R13 = 3.9e3; RVR1 = 4.7e3; R14 = 5.1e3; R15 = 18e3;
C1 = 1e-6;
// device constants (physical estimates, validated against the SPICE
// referee - see tests/test_relctl_spice.py header)
VBE = 0.65;    // base-conduction corner of the 100k/100k dividers
BETA = 300.0;  // 2SC945 house nominal
VSAT1 = 0.143; // Q1 Vce(sat) at forced beta ~130 (the damped floor)
VSAT2 = 0.095; // Q2 Vce(sat) under the half-damp branch current

dt = 1.0 / ma.SR;

// RELEASE node (no capacitor - algebraic): R7 pull-up loaded by R9 to the
// HD node and R8 into Q2's conducting base (stiff at VBE; the node sits
// >5 V whenever the switch is open, far above the 2*VBE corner, so Q2's
// base is always conducting then - and its overdrive is ~40x the collector
// demand, so q2on = not(release) with no intermediate region).
nodeA(b) = ba.if(relsw, 0.0, (VCC / R7 + b / R9 + VBE / R8) * rpar)
with { rpar = 1.0 / (1.0 / R7 + 1.0 / R9 + 1.0 / R8); };

// HD node = C1 state. Closed switch shorts it (tau ~ 2 us -> immediate at
// SR); open, it integrates the 100k-lattice charge balance: in via R9,
// out via R10 into the R11/base divider (base off below the 2*VBE corner).
nodeB = bstep ~ _
with {
    iout(b) = ba.if(b <= 2.0 * VBE, b / (R10 + R11), (b - VBE) / R10);
    bstep(b) = ba.if(hdsw, 0.0,
                     b + ((nodeA(b) - b) / R9 - iout(b)) * dt / C1);
};

// output node: R14/R15 divider + Q2's saturated R13+VR1 branch when
// RELEASE is open + Q1 as a BETA*ib current sink clipped at its
// saturation floor (the piecewise solve of the netlist's output node)
q2on = 1 - relsw;
rq2 = R13 + RVR1 * halfd;
ib1 = max(0.0, (nodeB - VBE) / R10 - VBE / R11);
gout = 1.0 / R14 + 1.0 / R15 + q2on / rq2;
vnoq1 = (VCC / R14 + q2on * VSAT2 / rq2) / gout;
process = max(VSAT1, vnoq1 - BETA * ib1 / gout);
