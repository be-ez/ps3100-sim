// KLM-63 WAVE FORM CONTROL, real-time model of the two instrument-wide
// waveform rails.
//
// Mirrors netlists/klm63-wavectl.cir:
//   the panel selector energizes one input; a D103-clamped resistor-divider
//   ladder plus diode-OR sets a DC pair on two smoothed 0.047u/1M rails,
//   each behind a buffer:
//     WFR (pin 12, "ramp" rail): +14.83V saw (Q101 clamp), 8.7..13.5V
//       triangle (TRI ADJ), ~0.36V leak in the pulse positions;
//     WFD (pin 13, "duty" rail): 7.37/8.99/11.52V for the three fixed pulse
//       widths, zener-offset PWM law in the multi/PWM position, ~0.4V leak
//       in triangle, 0V in saw.
//
// All DC levels below are SPICE-anchored (ngspice op grid of the netlist;
// tests/test_wavectl_spice.py holds them to hand theory, and
// tests/test_wavectl_dsp.py holds this file to SPICE within 60 mV).
//
// Dynamics: each rail is an RC (0.047u against source-resistance-when-
// steered / 1M-when-released), i.e. a fast-attack slow-release one-pole:
// attack tau = C*(Rsrc || 1M) with Rsrc the Thevenin of the selected
// divider plus the steering diode's incremental resistance; release tau =
// C*R113 (or R117) = 47 ms. SPICE transient referee: pin-19 attack 10-90%
// 320 us, release tau 46.9 ms; PWM-path step tau ~1 ms.
//
// One input: PWM IN (pin 15), volts (added to the pwm_dc test/CV offset,
// active only in the multi/PWM position with pwm_on = 1). One output,
// probe-selected (offline harness prints a single channel): probe 0 = WFR,
// probe 1 = WFD, volts at the board pins.
import("stdfaust.lib");

// --- controls ---
// panel selector position: 0 triangle (pin 14), 1 sawtooth (pin 11),
// 2/3/4 pulse wide/mid/narrow (pins 19/18/17), 5 multi-pulse/PWM (pin 16)
wave = nentry("wave", 1, 0, 5, 1);
// TRI ADJ trimmer VR101 (1 = wiper at top = highest WFR)
tri_adj = hslider("tri_adj", 0.5, 0.0, 1.0, 0.001);
// 1 = PWM IN jack driven (signal input + pwm_dc); 0 = jack open (the R112
// 150K / zener chain rests the rail at the "floating" level)
pwm_on = nentry("pwm_on", 0, 0, 1, 1);
pwm_dc = hslider("pwm_dc", 0.0, -14.9, 14.9, 0.001);
// output select for the single-channel offline harness
probe = nentry("probe", 0, 0, 1, 1);

// --- SPICE-anchored DC levels (volts at the pins) ---
// triangle: wiper = Vd103 + Ichain*(7.5k + 4.7k*t) minus the D102 drop;
// affine fit of the netlist op over t (max error ~20 mV), capped at the
// 1458 swing limit (+13.5V) which the top of the trimmer range runs into
wfrTri = min(8.668 + 5.294 * tri_adj, 13.5);
wfrSaw = 14.826;              // Q101 saturated clamp (14.9 - Vec_sat)
// pulse-position WFR leak: the D103 clamp rail reaches node X through the
// idle triangle chain and D102 (sub-uA, hence the sub-0.6V diode drop)
wfrLeakW = 0.356;
wfrLeakM = 0.360;
wfrLeakN = 0.366;
wfrLeakX = 0.004;             // pin-16 position: ladder rail is not lifted
wfdLeakTri = 0.396;           // clamp-rail leak through the idle dividers
// fixed pulse widths: divider junction (D103 clamp + R-pair) minus D-OR drop
wfdWide = 7.366;              // 5.6k / 5.6k
wfdMid = 8.989;               // 3.9k / 6.2k
wfdNarrow = 11.516;           // 1.8k / 6.8k
wfdFloat = 9.948;             // pin 16, PWM jack open (R112 to -14.9V)
// PWM law (jack driven): affine in the PWM voltage (fit of the netlist op,
// max error ~40 mV over -5..+6V), ceilinged by the IC11b swing limit
// through Q102's Vbe (13.5 - 0.67), floored at diode cutoff
pwmWfd(p) = max(0.0, min(9.292 + 0.525 * p, 12.833));

// --- rail dynamics ---
// attack source resistances (Thevenin of the energized branch + steering
// diode incremental resistance at its uA-scale load current)
rsrcWfr = (2.6e3, 60.0, 3.0e3, 3.0e3, 3.0e3, 3.0e3) : ba.selectn(6, wave);
// (saw clamps the output net directly - effectively instant)
rsrcWfd = (3.0e3, 3.0e3, 3.15e3, 2.74e3, 1.77e3, 21.6e3) : ba.selectn(6, wave);
crail = 0.047e-6;
rleak = 1.0e6;                // R113 / R117
tauRise(r) = crail * (r * rleak / (r + rleak));
tauFall = crail * rleak;      // 47 ms: diodes block, only 1M discharges
coef(tau) = 1.0 - exp(-1.0 / (tau * ma.SR));
asym(tr, x) = loop ~ _ with {
    loop(yp) = yp + (x - yp) * ba.if(x > yp, coef(tr), coef(tauFall));
};

// Both rails at once, volts at the pins: (WFR pin 12, WFD pin 13). The panel
// consumer (dsp/panelctl.dsp) drives a signal generator from both rails
// simultaneously, which the probe-selected `process` below cannot express -
// `probe` is a single control, so referencing `process` twice would select
// the same rail twice. `process` is unchanged and stays what the offline
// harness and the SPICE referee drive.
rails(p) = wfrOut, wfdOut
with {
    pv = pwm_dc + p;
    wfdPwm = ba.if(pwm_on > 0.5, pwmWfd(pv), wfdFloat);
    wfrT = (wfrTri, wfrSaw, wfrLeakW, wfrLeakM, wfrLeakN, wfrLeakX)
           : ba.selectn(6, wave);
    wfdT = (wfdLeakTri, 0.0, wfdWide, wfdMid, wfdNarrow, wfdPwm)
           : ba.selectn(6, wave);
    wfrOut = asym(tauRise(rsrcWfr), wfrT);
    wfdOut = asym(tauRise(rsrcWfd), wfdT);
};

process(p) = rails(p) : mix
with { mix(r, d) = r * (1.0 - probe) + d * probe; };
