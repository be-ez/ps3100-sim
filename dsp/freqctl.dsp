// KLM-62D FREQUENCY CONTROL (PS-3100/3300), real-time model of the
// master-pitch / TEMPERAMENT BUS board.
//
// Mirrors netlists/klm62d-freqctl.cir:
//
//   IC41b sums MOD R1/R2 (pins 43/44, the panel FREQ CONT buses shared
//   with KLM-63 MG1) at -1 each; IC41a sums COARSE (-0.122), FINE
//   (-0.0213), MOD1/MOD2 (-1), IC41b out (-2.006 -> MOD R nets +2.006)
//   and the -14.9 V rail offset (+0.677 V), with C401 giving a 1.59 kHz
//   pole. R413/R416 attenuate by 1/52 into the right base of the Q401
//   2SA798 matched-PNP pair; the IC42a/C402 integrator servos the common
//   emitters so the grounded-base left half carries exactly
//       I_ref = 14.9 V / (620k + 470k*ttune)          [13.7..24 uA]
//   and the right half delivers Ic = I_ref * exp(-Vb/VT), i.e. an
//   exponential pitch law at ln2*52*VT ~= 0.932 V/oct on the MOD pins.
//   The IC42b output servo (feedback R423 100k from the collector sense
//   node to the BUS, verified at full resolution) forces
//       V(bus) = V(rail) - 100k * Ic                  [100 mV per uA]
//   where the rail is Q402's emitter: an NPN follower whose base sits on
//   the VR402 "LINEALITY ADJUST" wiper (0..-26.5 mV) and which sources
//   the null current (100k/72k)*Ic, so its Vbe adds a logarithmic bend
//   (~ -60 mV/decade of Ic) and the -2 mV/K tempco that temperature-
//   compensates the bus driver. Q403 (zener-level-shifted common emitter
//   on -15 V) sinks the bus current; the bus is REGULATED (sub-ohm) until
//   a load draws more than |V(bus)|/10k + (100k/72k)*Ic, and tops out
//   near V(rail) (~ -0.5 V) as Ic -> 0.
//
// The model computes the same fixed-point the SPICE deck settles to,
// using the deck's device constants (IS=10f, BF=250/300, VAF=100),
// including the first-order Early corrections; converges in 3 unrolled
// iterations because every correction enters logarithmically. Neglected
//:
// Q401-right base current into the 98R base Thevenin, Q402 base current
// into the <500R wiper impedance.
//
// Dynamics modeled: C401's 1.59 kHz pole on the summed CV (the only
// corner below the audio band). NOT modeled (documented, control-rate
// board): the IC42a servo-dropout shelf above ~430 Hz (incremental expo
// gain x0.73, see tests/test_freqctl_spice.py::test_mod1_ac_response)
// and the >15 kHz loop poles (C403/C404).
//
// Output: pin 37 TEMPERAMENT BUS, volts (~ -9.3 .. -0.5 V; -1.6 V at
// panel-neutral defaults). Controls are pin volts / trimmer travel; pot
// wiring for pins 39/40/43/44 lives on an absent panel sheet, so the
// pins are exposed directly like the netlist's sources.
import("stdfaust.lib");

vcoarse = hslider("coarse", 0.0, -14.9, 14.9, 0.001);  // pin 39, V
vfine = hslider("fine", 0.0, -14.9, 14.9, 0.001);      // pin 40, V
vmod1 = hslider("mod1", 0.0, -5.0, 5.0, 0.001);        // pin 41, V
vmod2 = hslider("mod2", 0.0, -5.0, 5.0, 0.001);        // pin 42, V
vmodr1 = hslider("modr1", 0.0, -5.0, 5.0, 0.001);      // pin 43, V
vmodr2 = hslider("modr2", 0.0, -5.0, 5.0, 0.001);      // pin 44, V
ttune = hslider("ttune", 0.5, 0.0, 1.0, 0.001);        // VR401 TOTAL TUNE
lin = hslider("lin", 0.5, 0.0, 1.0, 0.001);            // VR402 LINEALITY
iload = nentry("iload", 0.0, -0.002, 0.002, 1e-7);     // A drawn from bus (test hook)

// --- device/model constants (identical to the netlist models) ---
VT = 0.025865;    // kT/q at the deck's 27 C
ISQ = 1e-14;      // QC945 / QA798 IS
BF945 = 300.0;
VAF = 100.0;

// --- IC41b / IC41a summing amps (op-amp swing +-13.5 V) ---
clip41(x) = max(-13.5, min(13.5, x));
o41b = clip41(0.0 - (vmodr1 + vmodr2));
r406p = 51e3 * 2.2e6 / (51e3 + 2.2e6);  // R406 || R428
o41aDC = clip41(
    14.9 * 100e3 / 2.2e6
    - 100e3 / 820e3 * vcoarse
    - 100e3 / 4.7e6 * vfine
    - vmod1 - vmod2
    - 100e3 / r406p * o41b);
// C401 across R412: one-pole lowpass at 1.59 kHz on the summed CV
fc401 = 1.0 / (2.0 * ma.PI * 100e3 * 1e-9);
o41a = o41aDC : si.smooth(exp(-2.0 * ma.PI * fc401 / ma.SR));

// --- exponential converter ---
vb = o41a * 100.0 / 5200.0;              // R413/R416 divider
iref = 14.9 / (620e3 + 470e3 * ttune);   // IC42a servo law
// left-half Veb with its Early term (collector at the servo virtual gnd);
// two unrolled fixed-point steps (logarithmic -> instant convergence)
veblOf(v) = VT * log(iref / (ISQ * (1.0 + v / VAF)));
vebl = veblOf(veblOf(veblOf(0.55)));

// --- Q402 lineality/temperature-compensation follower: wiper voltage ---
// VR402 1k || R426 100R from G3 to the R427 51k tap into -14.9 V
rparw = 1.0 / (1.0 / 1000.0 + 1.0 / 100.0);
va402 = -14.9 * rparw / (rparw + 51e3);
vw = va402 * lin;

// --- fixed point of the regulated-bus law (3 unrolled iterations) ---
// step: rail -> (Ic with Early ratio, Q402 emitter current and Vbe)
icOf(rail) = iref * exp(0.0 - vb / VT) * (1.0 + (vebl - rail) / VAF) / (1.0 + vebl / VAF);
railOf(rail) = vw - vbe402
with {
    ic402 = icOf(rail) * (100.0 / 72.0) * BF945 / (BF945 + 1.0);
    ncq = 14.9 * 3.3 / 15.3 - (12e3 * 3.3e3 / 15.3e3) * ic402;
    vbe402 = VT * log(max(ic402, 1e-12) / (ISQ * (1.0 + (ncq - rail) / VAF)));
};
rail1 = railOf(-0.55);
rail2 = railOf(rail1);
rail3 = railOf(rail2);
busReg = rail3 - 100e3 * icOf(rail3);

// --- load capacity / out-of-regulation behavior ---
// Q403 only sinks: if the drawn current exceeds the sink demand the loop
// opens (op-amp bottoms, zener off) and the bus relaxes to the passive
// network R424 || (R420+R421 from the still-Q402-held rail) || (R423 from
// the collector sense node, which floats ~1k*Ic above the rail).
// Exact 2-node solve with rail held at the regulated-solve value (Q402's
// Vbe shift at the different emitter current costs ~20 mV, inside the
// documented saturation tolerance).
busOff = (rail3 / 72e3 + (rail3 * g1 + icf) * g3 / (g1 + g3) - iload)
       / (gb + g3 - g3 * g3 / (g1 + g3))
with {
    g1 = 1.0 / 1e3;    // R417
    g3 = 1.0 / 100e3;  // R423
    gb = 1.0 / 10e3 + 1.0 / 72e3;  // R424, R420+R421
    icf = icOf(rail3);
};
bus = max(min(busReg, busOff), -14.2);  // -14.2: Q403/LM358 bottoming

process = bus;
