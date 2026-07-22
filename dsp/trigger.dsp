// KLM-76 KBD TRIGGER (PS-3100), real-time model.
//
// Mirrors netlists/klm76-trigger.cir.
//
// The raw keyboard trigger lands on a bus that idles just above ground and is
// driven POSITIVE as keys are held (behavioural here: the KLM-69 driver and
// the three input resistors are off-sheet). Three 1/2-4558 comparators square
// it into three ACTIVE-LOW outputs (each jack idles ~+4.1 V and is pulled to
// GND to trigger; +4.1 V = the 13.5 V comparator swing through the diode +
// RO 300 + R216 1.8k + R218 1k output divider):
//
//   - internal TRIG OUT (pin 33): bus vs the panel SELECT ladder tap
//     (OFF, positions 1..5 = 130/261/391/522/652 mV). DC -> a LEVEL held for
//     the whole time the bus is above the tap. SELECT = trigger sensitivity.
//   - SINGLE (pin 31): bus vs FIXED +98.7 mV (R204/R214). DC -> a LEVEL,
//     asserted continuously while ANY key is held => the downstream envelope
//     fires once and sustains; legato does NOT retrigger.
//   - MULTIPLE (pin 30): bus AC-coupled (C203 0.068 into R206 270k, tau
//     ~18.4 ms) vs FIXED +67.4 mV (R207b/R215) -> a PULSE on every positive
//     bus EDGE, i.e. every new key attack, even in legato => retrigger per
//     key. Pulse width ~ tau*ln(step/67.4mV) ~ tens of ms; no pulse on
//     release (negative edges don't cross the positive threshold).
//
// This is the SPICE-refereed model: it is driven with the SAME key schedule
// as netlists/klm76-trigger.cir (tests/test_trigger_dsp.py) and reproduces the
// bus DC levels (from the R202/R205/Rkbd divider) and the three conditioned
// outputs. Key activity is given as up to three key on/off times (seconds) -
// the abstract "key-gate signals"; the bus is synthesised the same way as the
// netlist's behavioural keyboard (one step per held key).
import("stdfaust.lib");

// --- key schedule (seconds); each held key adds one Vstep to the bus ---
k1on  = hslider("k1on",  0.05, 0.0, 1e6, 1e-6);
k1off = hslider("k1off", 0.45, 0.0, 1e6, 1e-6);
k2on  = hslider("k2on",  0.15, 0.0, 1e6, 1e-6);
k2off = hslider("k2off", 0.30, 0.0, 1e6, 1e-6);
k3on  = hslider("k3on",  1e9,  0.0, 1e9, 1e-6);
k3off = hslider("k3off", 1e9,  0.0, 1e9, 1e-6);
Vstep = hslider("Vstep", 10.0, 0.0, 30.0, 1e-6);   // keyboard drive per key
Rkbd  = hslider("Rkbd",  10e3, 1.0, 1e6, 1.0);      // keyboard series R
possel = hslider("possel", 3, 0, 5, 1);            // SELECT: 0=OFF, 1..5
selout = hslider("selout", 0, 0, 2, 1);            // 0=trigout 1=single 2=mult

// --- circuit constants (traced) ---
VCC = 14.9;
VP15 = 15.0;
LADDER_I = VP15 / (11e3 + 5*100.0);          // 1.3043 mA
VTH_SINGLE = VCC * 1e3 / (1e3 + 150e3);      // R204/R214  -> 98.68 mV
VTH_MULT   = VCC * 1e3 / (1e3 + 220e3);      // R207b/R215 -> 67.42 mV
HI = (13.5 - 0.68) * 1e3 / (300.0 + 1.8e3 + 1e3);  // active-low idle-high ~4.135 V
tauMult = (270e3 + 900.0) * 0.068e-6;        // R206(+bus Thevenin) * C203 ~18.4 ms
tauEdge = 1.5e-4;                            // bus edge (tanh tr + R201/C201) smoothing

dt = 1.0 / ma.SR;
tsec = float(ba.time) / ma.SR;

// --- behavioural bus: one Vstep per held key, through the R202/R205/Rkbd DC
//     divider (matches the netlist's static bus level exactly) ---
key(on, off) = (tsec >= on) & (tsec < off);
count = key(k1on, k1off) + key(k2on, k2off) + key(k3on, k3off);
vkbd = Vstep * count;
busDC = (VCC/300e3 + vkbd/(100.0 + Rkbd)) / (1.0/300e3 + 1.0/1e3 + 1.0/(100.0 + Rkbd));
bus = busDC : si.smooth(ba.tau2pole(tauEdge));

// --- SELECT tap threshold (OFF -> unreachable) ---
tapThr = ba.if(possel < 0.5, 1e9, LADDER_I * possel * 100.0);

// --- IC22b AC coupling: 1-pole highpass y[n] = a*y[n-1] + x[n]-x[n-1] ---
onehp(a) = (_ <: _ - mem) : (+ ~ *(a));
acn = bus : onehp(exp(-dt / tauMult));

// --- comparators -> active-low output levels (asserted = pulled to GND) ---
trigAssert = bus > tapThr;
sngAssert  = bus > VTH_SINGLE;
mulAssert  = acn > VTH_MULT;
level(assert) = HI * (1.0 - assert);

trigout = level(trigAssert);
single  = level(sngAssert);
mult    = level(mulAssert);

// single selectable output channel (the offline harness reads channel 0)
process = ba.selectn(3, int(selout), trigout, single, mult);
