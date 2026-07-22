// KLM-76 SAMPLE & HOLD (PS3100/3300) - real-time model.
// Referee: netlists/klm76-sh.cir; modeling decisions and the DSP-vs-SPICE
// comparison method live in tests/test_sh_dsp.py.
//
// Signal path (mirrors the netlist):
//   S/H IN -> IC1b buffer (gain 1 + R17/R16 = 1.1) -> Q7 2SK30-GR JFET
//   series switch (closed for ~1.33 ms once per clock period) -> C9 0.1u
//   hold cap -> CA3140 buffer -> S/H OUT.
// Clock: the traced Q1-Q4 oscillator (2026-07-21 full-res re-read) is an
// exponential converter (Q3/Q4 pair, pot through R9 into the ~1.5k R12 node
// => ~146 mV span at Q4's emitter => e^(V/VT) current law) ramping C6 0.1u
// down between the Q1/Q2 latch thresholds. The panel law is therefore
// EXPONENTIAL and the clock never stalls:
//   f = F0 * exp(K * (pot - 0.5)),  F0/K fitted to SPICE sweeps of the
// transistor-level netlist (residual < 1.5% over the full pot travel);
// F0 = the "~1.2 Hz AT CENTER POSITION" panel annotation (VR1 FREQ.ADJ
// calibration, vr1pos=0.512 in the netlist). Range ~0.072 .. 19.3 Hz.
// Sample pulse width = C7*(R13||R14)*ln(dV_ramp/1.354V) ~ 1.33 ms (IC1a
// differentiator, SPICE-measured).
import("stdfaust.lib");

// ---- constants mirroring netlists/klm76-sh.cir ----
f0Center = 1.2;                     // Hz at pot center (panel annotation;
                                    // VR1 calibration in the netlist)
kExpo   = 5.588;                    // d ln f / d pot (SPICE sweep fit; theory
                                    // ~ 10V * (R12||..)/R9 / VT = 5.6)
tPulse  = 1.33e-3;                  // sample pulse width, SPICE-measured
                                    // (~ C7*(R13||R14)*ln(6.2/1.354))
ronJfet = 185.0;                    // 2SK30-GR on resistance ~ 1/(2*Beta*|Vto|)
c9      = 0.1e-6;                   // hold capacitor
tauAcq  = ronJfet * c9;             // acquisition time constant ~ 18.5 us
gainBuf = 1.1;                      // IC1b: 1 + R17/R16 = 1 + 10k/100k
vRail   = 14.4;                     // op-amp swing limit on the 14.9V rails

// ---- panel control + test hooks ----
clockpos  = hslider("clock", 0.5, 0.0, 1.0, 0.001);   // CLOCK FREQ pot (10KB)
clock_hz  = hslider("clock_hz", 0.0, 0.0, 100.0, 0.001); // hook: >0 overrides law
droop     = hslider("droop", 1.0e-4, 0.0, 100.0, 1e-6);  // hook: V/s, default
                                                         // = CA3140 Ib/C9
// deterministic internal test sources (the offline driver feeds impulses,
// so transient comparisons use these): 0 = external input, 1 = ramp,
// 2 = sine, 3 = DC level
testmode   = nentry("testmode", 0, 0, 3, 1);
ramp_slope = hslider("ramp_slope", 1.0, 0.0, 100.0, 0.001); // V/s
sine_hz    = hslider("sine_hz", 0.3, 0.01, 20.0, 0.001);
sine_amp   = hslider("sine_amp", 2.0, 0.0, 10.0, 0.001);
dc_level   = hslider("dc_level", 2.0, -10.0, 10.0, 0.001);

// ---- clock law (exponential converter, fitted to the SPICE netlist) ----
fpanel = f0Center * exp(kExpo * (clockpos - 0.5));
fclk = ba.if(clock_hz > 0.0, clock_hz, fpanel);

// sampling pulse: first tPulse seconds of every clock period
ph = (+(fclk / ma.SR) : ma.frac) ~ _;
pulse = ph < fclk * tPulse;

// ---- S&H core ----
// during the pulse the hold cap tracks the buffered input through the JFET
// Ron (tau ~ 18.5 us, fully settled within the 1.33 ms pulse); during hold it
// droops linearly (constant bias current out of C9), clamped at the rails
acqCoef = 1.0 - exp(-1.0 / (ma.SR * tauAcq));
core(x) = (\(y).(ba.if(pulse,
                       y + acqCoef * (gainBuf * x - y),
                       max(-vRail, min(vRail, y - droop / ma.SR))))) ~ _;

// ---- test sources ----
ramp  = +(ramp_slope / ma.SR) ~ _;
sine  = sine_amp * os.osc(sine_hz);
tm    = int(testmode);
src(x) = select2(tm >= 2,
                 select2(tm >= 1, x, ramp),
                 select2(tm >= 3, sine, dc_level));

process = \(x).(core(src(x)));
