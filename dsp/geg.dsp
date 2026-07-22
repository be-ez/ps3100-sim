// KLM-76 General Envelope Generator (PS-3100/3300), real-time model.
//
// Mirrors netlists/klm76-geg.cir:
//
//   gate -> delay timer (C102 ramps from its ~-12.6 V reset clamp to the
//   grounded-input comparator threshold 0 V; PNP pair Q101/Q102 steered by
//   the DELAY pot) -> attack (PNP pair Q103/Q104, tail switched by the
//   delay comparator, charges C103 linearly from the -5.89 V floor up to
//   the +0.66 V saturation ceiling) -> sustain while the gate is held ->
//   release (NPN pair Q105/Q106 riding a buffered -5.22 V reference sinks
//   C103 back to the floor). Segments are LINEAR ramps (constant-current):
//   a trapezoid, per the panel name. OUT2 is the envelope level-shifted
//   through the D106 5.1 V zener + Q107 follower: out2 = 0.8912*env + 5.281
//   (0.03 V floor .. 5.87 V top with the netlist's VR104 level trim).
//
// All constants below are FITTED TO THE SPICE REFEREE (ngspice sweeps of
// the traced netlist over each 10kB panel pot; validation in
// tests/test_geg_dsp.py). Segment cap currents follow the diff-pair
// steering law of the traced network,
//     I(k) = Imax / (1 + exp(s*(k-k0)*(1 + q*(k-k0)))),   k = pot 0..1
// (q absorbs pot loading / base-current shifts; fit residual <= 0.7 %).
// Panel ranges with the netlist's ADJ trim defaults:
//   delay ~14 ms..9.5 s, attack ~10 ms..11.3 s, release ~8 ms..7.8 s.
// NOTE the traced release sense: krel = 1 is FAST (R121's standing offset
// against the R122 pot feed - opposite to delay/attack; see model doc).
// Sustain (+0.6576 V) and floor (-5.893 V) are saturation-limited and
// current-INdependent (measured spread < 4 mV across both sweeps); the
// old zener-knee level laws are retracted with the reconstruction.
//
// Output: OUT 2 (pin 16), the 0 -> +5.87 V trapezoid, in volts. The other
// panel outputs are affine copies of env: OUT1 ~= 0.986*env (rests at
// -5.8 V), /OUT1 ~= -0.986*env.
// Controls: gate (0/1; the board's TRIG IN pin is active-low - the gate
// here is the abstract "envelope on" state), delay/attack/release pots 0..1.
// Test hooks gate_on/gate_off (seconds): when gate_off > gate_on an
// internal timer generates the gate so an offline render
// (tests/impulse_driver.cpp) can produce one full cycle in a single run.
import("stdfaust.lib");

kdel = hslider("delay", 0.0, 0.0, 1.0, 0.001);
katt = hslider("attack", 0.3, 0.0, 1.0, 0.001);
krel = hslider("release", 0.3, 0.0, 1.0, 0.001);
gatec = hslider("gate", 0, 0, 1, 1);
gon = hslider("gate_on", 0.0, 0.0, 1e6, 1e-6);   // s, test hook
goff = hslider("gate_off", 0.0, 0.0, 1e6, 1e-6); // s, test hook

tsec = float(ba.time) / ma.SR;
gate = ba.if(goff > gon, (tsec >= gon) & (tsec < goff), gatec > 0.5);

// diff-pair steering law, constants fitted to ngspice (see header)
law(imax, s, k0, q, k) = imax / (1.0 + exp(u))
with {
    d = k - k0;
    u = s * d * (1.0 + q * d);
};
idel = law(1.74381e-3, 6.2846, 0.0082938, 0.15379, kdel);
iatt = law(2.3171e-3, 6.2945, -0.14972, 0.12573, katt);
irel = law(3.62285e-3, -7.6066, 1.1659, 0.049156, krel); // s<0: k=1 fast

Ct = 1e-6;        // C102 = C103 = 1 uF/25 V Ta
dt = 1.0 / ma.SR;

// --- delay timer: C102 voltage (traced: comparator threshold = 0 V) ---
vth = 0.0;        // IC11b "-" input is grounded
dceil = 0.66;     // Q102 saturation past the threshold
resetPole = exp(-dt / 0.0015); // (R104 1k + comparator Ro) * 1 uF
resetTarget = -12.6;           // cmp1 low + D103/R104 drops (law-fit span)
// state = dcap - resetTarget so the recursion's zero initial state is the
// settled reset clamp (idle), not an artificial mid-scale start.
// Parameterized on the gate as a SIGNAL; process below still uses the
// slider/timer gate, bit-identically.
dcapOf(g) = resetTarget + (dstep ~ _)
with {
    dstep(s) = ba.if(g, min(s + idel * dt / Ct, dceil - resetTarget),
                     s * resetPole);
};

// --- envelope: C103 voltage, linear ramps between saturation levels ---
sus = 0.6576;     // Q104 saturation ceiling (current-independent)
floorv = -5.893;  // Q106 saturation floor around the -5.22 V reference
// First-order Early-effect tilt (measured from SPICE mid-sweeps): the
// active device's collector rides env, so the ramp current droops as its
// Vce shrinks - attack -1.19 %/V as env rises, release -0.96 %/V as env
// falls. The steering laws are mid-ramp fits (env ~ -2.62 V), hence the
// correction is centered there. Keeps slow-attack t_on within tolerance.
envmid = 0.5 * (sus + floorv);
tiltA(e) = 1.0 - 0.0119 * (e - envmid);
tiltR(e) = 1.0 + 0.0096 * (e - envmid);
// state = env - floorv: zero initial state = idle floor
envRawOf(g) = floorv + (estep ~ _)
with {
    run = dcapOf(g) > vth;
    estep(s) = ba.if(run, min(s + iatt * tiltA(floorv + s) * dt / Ct, sus - floorv),
                     max(s - irel * tiltR(floorv + s) * dt / Ct, 0.0));
};

// --- OUT2 level shifter (D106 zener + VR104 wiper + Q107 follower) ---
envOf(g) = 0.8912 * envRawOf(g) + 5.2811;
process = envOf(gate);
