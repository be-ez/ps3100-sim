// KLM-63 FILTER CONTROL (PS3100/3300), real-time model.
//
// Mirrors netlists/klm63-filterctl.cir.  SPICE-refereed in
// tests/test_filterctl_dsp.py by a DC-grid comparison: the board is purely
// resistive (not one capacitor in the CV path), so it shapes no dynamics and
// the model is exact memoryless algebra.
//
// Signal law (all constants from the netlist, junctions verified at full
// res 2026-07-21):
//   vo1 = clip( ofs(fcadj) - (vfc + vmod1 + vmod2) )       IC1a summer
//   vo2 = clip( -vo1 + 0.8*vbal )                          IC1b (FCU amp)
//   vo3 = clip( -vo1 - (2/3)*vbal )                        IC2a (FCL amp)
//   FCU pin 9  = 0.019324*vo2 - 0.215942                   (Zout 14.49 ohm)
//   FCL pin 10 = 0.019417*vo3 - 0.144660                   (Zout 14.56 ohm)
//   FC BIAS pin 4 = -14.9 V (open-circuit; -14.9 behind 10k)
// ofs(fcadj): the FC ADJ (VR1) loaded-wiper offset, +4.33..+11.20 V.
// clip(): +/-13 V 4558 output clamp - this bounds the buses to
// FCU -0.467..+0.035 V, FCL -0.397..+0.108 V, the absolute range any
// KORG35 cell can see.  BAL moves FCU and FCL in OPPOSITE directions
// (complementary upper/lower balance crossfade).
//
// Controls (this board's interface; panel wiring of the FC slider and the
// BAL source is cross-board, so all pins are exposed in volts):
//   vfc, vmod1, vmod2, vbal : pin 5/6/7/8 voltages (V), 100k input Z each
//   fcadj  : VR1 "FC ADJ" 0..1 (0 = least offset/brightest bus, 1 = most)
//   outsel : 0 FCU (pin 9), 1 FCL (pin 10), 2 FC BIAS (pin 4)
// Audio-rate inputs: process(m1, m2) adds m1/m2 (volts) to vmod1/vmod2 so
// the MG1/MOD-VCA mod buses can drive the board at signal rate.
import("stdfaust.lib");

// --- controls ---
vfc = hslider("vfc", 0.0, -15.0, 15.0, 0.001);
vmod1 = hslider("vmod1", 0.0, -15.0, 15.0, 0.001);
vmod2 = hslider("vmod2", 0.0, -15.0, 15.0, 0.001);
vbal = hslider("vbal", 0.0, -15.0, 15.0, 0.001);
fcadj = hslider("fcadj", 0.5, 0.0, 1.0, 0.001);
outsel = nentry("outsel", 0, 0, 2, 1);

// --- constants (netlist values) ---
vneg = -14.9;
vsat = 13.0;                       // 4558 output clamp (see netlist header)
clip(x) = max(-vsat, min(vsat, x));

// FC ADJ offset: G1 - R12 100k - VR1 100k - R14 22k - -14.9V, wiper -> R13
// 100k into the IC1a virtual ground (loaded-wiper Thevenin, gain -1)
pa = 100.0e3 + 100.0e3 * fcadj;    // ground side of the wiper
pb = 100.0e3 * (1.0 - fcadj) + 22.0e3;  // -14.9 V side
ofs = -vneg * (pa / (pa + pb)) * 100.0e3 / (pa * pb / (pa + pb) + 100.0e3);

// output pads: bus = k*vo + o
gu = 1.0/750.0 + 1.0/1.0e3 + 1.0/15.0;       // R20/R21/R22
ku = (1.0/750.0) / gu;
ou = (vneg/1.0e3) / gu;
gl = 1.0/750.0 + 1.0/1.5e3 + 1.0/15.0;       // R26/R27/R28
kl = (1.0/750.0) / gl;
ol = (vneg/1.5e3) / gl;

// --- board law ---
law(m1, m2) = fcu, fcl, fcbias
with {
    vo1 = clip(ofs - (vfc + vmod1 + m1 + vmod2 + m2));
    vo2 = clip(-vo1 + 0.8 * vbal);           // BAL non-inv lift, R18/R19
    vo3 = clip(-vo1 - (2.0/3.0) * vbal);     // BAL inverting sum, R24
    fcu = ku * vo2 + ou;
    fcl = kl * vo3 + ol;
    fcbias = vneg;                           // open-circuit pin 4
};

process(m1, m2) = law(m1, m2) : ba.selectn(3, outsel);
