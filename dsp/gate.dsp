// KLM-69E gate channel + KORG35 low-pass, real-time model.
//
// Mirrors netlists/klm69-gate.cir. SPICE is the referee: the linear
// core below is a 4-parameter fit (input HPF corner fh, cutoff f0,
// resonance Q, gain g) of the netlist's AC response at a Vfc grid; fit
// residual <= 0.10 dB over the tested range (tests/test_gate_dsp.py).
//
// Channel structure (matching the netlist):
//   input -> CD4007 pass-gate VCA (envelope-driven, switch-like with a
//   narrow ~0.3 V linear transition that the envelope RC turns into a
//   click-free ms-scale ramp) -> C301 0.0022u coupling (a bass rolloff
//   that TRACKS the cutoff: the module input impedance is the
//   reverse-saturated transistor itself) -> KORG35 Sallen-Key 2-pole
//   (Q2/Q3 reverse-saturation resistors, JFET+PNP unity buffer) -> out.
//
// Dominant nonlinearity: the reverse-saturated Q2/Q3 junctions. Their
// linear signal-current budget is the small control current itself, so
// the cell saturates from ~10 mV at the core (mid-law default; the budget
// scales with the cutoff); the distortion is
// H2-dominant (asymmetric junction law). Modeled as an offset-tanh
// waveshaper between the tracking HPF and the resonant 2-pole, with scale
// A and asymmetry c calibrated against ngspice transient harmonics.
//
// Envelope: attack RC (C201 1u charged via ~100k from the conditioned
// ATTACK bus) and release RC (4.7M bleed, ~4.7 s max). The panel
// ATTACK/DECAY/SUSTAIN conditioning block is not transcribed; attack and
// release are exposed directly in seconds.
import("stdfaust.lib");

// --- controls -----------------------------------------------------------
// FC control. RESOLVED 2026-07-21 (KLM-63 filterctl model + gate-sheet
// re-read): the MS-10 47k/680 divider does not exist - the FCU/FCL buses
// are stiff mV-scale lines landing on the module CV pin directly (47R
// per-note ground return). The slider keeps its historical name/range
// (panel-volt feel, instrument.dsp compatibility); internally it maps
// LINEARLY onto the physical open-circuit blended-bus range
//   vfcu = 0.12 + vfc * (0.6 / 14)   (-14..0  ->  -0.48..+0.12 V)
// so vfc=-6 is the netlist default Vfcu=-0.1371 (f0 ~ 1.6 kHz). The law
// itself (exponential, ~17 oct/V of BUS voltage at the bottom compressing
// to ~7 oct/V at the top as the splitter saturates) is SPICE-fitted below.
vfc = hslider("vfc", -6.0, -14.0, 0.0, 0.01);
gate = hslider("gate", 0.0, 0.0, 1.0, 1.0);
attack = hslider("attack", 0.1, 0.001, 1.0, 0.001);   // RC seconds
// release RC seconds. This is what the KLM-62D "GATE RELEASE TERMINAL" sets on
// the real board (Vrel -> Q313 -> the per-note Q301 damp across C201): +11.6 V
// full release = ~4.7 s (R401 4.7M only, the default here); +5.8..8.0 V half
// damp = ~40 ms..hundreds of ms; +0.14 V damped = ~20 ms (Q301 saturated).
// Kept in seconds (existing name/range); the SPICE Vrel->tau law is the referee
// (tests/test_gate_spice.py::test_release_bus_*).
release = hslider("release", 4.7, 0.05, 10.0, 0.01);  // RC seconds
// EXPAND: per-note gate-envelope -> KORG35 cutoff (the signature pluck). Depth
// 0..1; the envelope pulls the module input node down through R501 33k + D301,
// raising this note's cutoff up to ~+2.25 oct at full depth+envelope. 0 = off
// (baseline unchanged, instrument.dsp path untouched). SPICE-fitted below.
expand = hslider("expand", 0.0, 0.0, 1.0, 0.01);      // EXPAND depth (per-note)
bypassEnv = checkbox("bypass_env");   // tests: force channel open, VCA = 1
bypassNl = checkbox("bypass_nl");     // tests: linear core only (the offline
                                      // driver's unit impulse is ~300x the
                                      // core clip scale; SPICE AC is
                                      // linearized, so compare linearly)
// test oscillator: replaces the input when amp > 0 (the offline impulse
// driver can only inject an impulse; the distortion tests need a sine)
oscAmp = nentry("testosc_amp", 0.0, 0.0, 10.0, 0.001);
oscFreq = nentry("testosc_freq", 500.0, 10.0, 20000.0, 0.1);
// test hook: force the envelope level seen by the EXPAND cutoff sweep so the
// impulse-response driver can measure a STEADY swept cutoff (the impulse lands
// at sample 0, before any real envelope has risen). < 0 = use the real
// envelope (normal operation).
expForce = nentry("expand_force", -1.0, -1.0, 1.0, 0.001);

// --- envelope (C201 1u + charge/bleed paths) ----------------------------
attPole = ba.tau2pole(attack);
relPole = ba.tau2pole(release);
envPole = ba.if(gate > 0.5, attPole, relPole);
env = gate : onepole
with {
    onepole(x) = y ~ _
    with {
        y(fb) = x + (fb - x) * envPole;
    };
};
envc = env * 10.0;   // envelope cap voltage, volts (charges toward ~10 V)

// --- gate VCA (CD4007 pass device, netlist M1/RVCA values) --------------
// envn = 14.9 - 1.6*envc (inverted drive line), PMOS VTO -2, KP 0.4m,
// 100k load: conduction starts at envc ~5.9 V and is ~unity by ~6.5 V.
envnV = 14.9 - 1.6 * envc;
vov = max(7.45 - envnV - 2.0, 0.0);
vcaGain = ba.if(vov > 1e-6, rl / (rl + 1.0 / (0.4e-3 * max(vov, 1e-6))), 0.0)
with {
    rl = 100e3;
};
vca = ba.if(bypassEnv > 0.5, 1.0, vcaGain);

// --- SPICE-fitted linear core -------------------------------------------
// grid: Vfcu = -0.48..+0.12 step 0.12 (the full physical bus range,
// netlist defaults, Rpeak open) = vfc slider -14..0 step 2.8; values from
// least-squares fits of ngspice AC curves. The fit
// model is the 2x-DISCRETIZED cascade as implemented below (bilinear,
// prewarped at each corner), over the test window 20 Hz..12 kHz within
// 35 dB of the peak: with the top fh corners now at 17..23 kHz the
// continuous-time and discretized shapes part company in-window, so
// fitting the implementation is what makes the DSP land on SPICE. Fit
// residual <= 0.10 dB except the -0.48 V edge (0.68 dB: f0 falls to 32 Hz
// and the tracking HPF eats the peak, as the old -12 V edge did).
// The -0.36 entry (index 1) was re-fit 2026-07-21 when the R501/D301 EXPAND
// path was added to the netlist: including that always-present coupling loads
// the module input node slightly and sharpens this high-Q edge (Q 14.79 ->
// 15.37, f0 131.0 -> 131.8), a physically-real shift the earlier deck omitted.
// Re-fit against the new SPICE baseline (continuous-time model; the corners
// here are low enough that discretized==continuous, maxres 0.049 dB). The
// higher gridpoints shifted <0.04 dB and are unchanged; the -0.48 edge (index
// 0, excluded from the DSP small-signal test as before) shifted <0.25 dB.
f0T = (32.0, 131.8, 552.6, 1856.2, 4363.5, 7652.4);
fhT = (202.8, 657.7, 2660.2, 8363.2, 16567.5, 23318.9);
qT = (5.05, 15.37, 9.46, 8.83, 7.49, 6.17);
gdbT = (7.34, 8.56, 9.04, 8.93, 8.51, 8.18);

gpos = max(0.0, min(5.0, (vfc + 14.0) / 2.8));
gi = min(4, int(gpos));
gt = gpos - gi;
lut(l) = it.interpolate_linear(gt, ba.selectn(6, gi, l), ba.selectn(6, gi + 1, l));
lutLog(l) = pow(2.0, lut(par(i, 6, log(ba.take(i + 1, l)) * 1.4426950408889634)));
// corners live at the 2x rate (sr2 = 2*SR): clamp at 0.9*SR = 0.45*sr2,
// comfortably below the 2x Nyquist (the top fh corner is now ~23.3 kHz)
f0 = min(lutLog(f0T), 0.9 * ma.SR);
fh = min(lutLog(fhT), 0.9 * ma.SR);
qq = lut(qT);
lingain = pow(10.0, lut(gdbT) / 20.0);

// --- 2x-oversampled core (idiom shared with dsp/resonator.dsp) ----------
// The tracking-HPF corner reaches ~23.3 kHz at the top of the real bus
// range: a host-rate bilinear cannot even represent it, so the HPF +
// nonlinearity + 2-pole all run at 2*SR between the same 63-tap Kaiser(8)
// halfband pair the resonator core uses. Oversampling the waveshaper also
// halves its aliasing.
sr2 = 2.0 * ma.SR;

tf2biq2x(b2, b1, b0, a1, a0, w1) = biq2x(b0d, b1d, b2d, a1d, a2d)
with {
    c = 1.0 / tan(w1 * 0.5 / sr2);
    csq = c * c;
    d = a0 + a1 * c + csq;
    b0d = (b0 + b1 * c + b2 * csq) / d;
    b1d = 2.0 * (b0 - b2 * csq) / d;
    b2d = (b0 - b1 * c + b2 * csq) / d;
    a1d = 2.0 * (a0 - csq) / d;
    a2d = (a0 - a1 * c + csq) / d;
};

// first-order bilinear at 2x, prewarped at w1: H(S) = (b1 S + b0)/(S + a0)
tf1biq2x(b1, b0, a0, w1) = biq2x(b0d, b1d, 0.0, a1d, 0.0)
with {
    c = 1.0 / tan(w1 * 0.5 / sr2);
    d = a0 + c;
    b0d = (b0 + b1 * c) / d;
    b1d = (b0 - b1 * c) / d;
    a1d = (a0 - c) / d;
};

biq2x(b0d, b1d, b2d, a1d, a2d) = (step2 ~ (_, _)) : (!, !, _, _)
with {
    step2(s1, s2, xe, xo) = s1b, s2b, ye, yo
    with {
        ye  = b0d * xe + s1;
        s1a = b1d * xe - a1d * ye + s2;
        s2a = b2d * xe - a2d * ye;
        yo  = b0d * xo + s1a;
        s1b = b1d * xo - a1d * yo + s2a;
        s2b = b2d * xo - a2d * yo;
    };
};

hbe = (
    -4.80305017271596550e-05, 2.18072446837770976e-04,
    -5.87120123543804530e-04, 1.27621266727555803e-03,
    -2.44415184627663068e-03, 4.29181686103045218e-03,
    -7.06882814109922007e-03, 1.10872933079856958e-02,
    -1.67524197610087605e-02, 2.46311644557551356e-02,
    -3.56093980907868646e-02, 5.12753672052194132e-02,
    -7.49787582233890082e-02, 1.15448081225674584e-01,
    -2.04885004141135557e-01, 6.34145702659188459e-01,
    6.34145702659188459e-01, -2.04885004141135557e-01,
    1.15448081225674584e-01, -7.49787582233890082e-02,
    5.12753672052194132e-02, -3.56093980907868646e-02,
    2.46311644557551356e-02, -1.67524197610087605e-02,
    1.10872933079856958e-02, -7.06882814109922007e-03,
    4.29181686103045218e-03, -2.44415184627663068e-03,
    1.27621266727555803e-03, -5.87120123543804530e-04,
    2.18072446837770976e-04, -4.80305017271596550e-05
);
firHbe(x) = sum(i, 32, ba.take(i + 1, hbe) * (x @ i));
up(x) = firHbe(x), (x @ 15);
downHB(ue, uo) = 0.5 * firHbe(ue) + 0.5 * (uo @ 16);

// --- EXPAND cutoff modulation (per-note pluck) --------------------------
// SPICE-fitted at the default op (vfc=-6, Vfcu=-0.1371): the gate envelope,
// scaled by the EXPAND depth, pulls the module input node down through
// R501 33k + D301, and the reverse-mode splitter operating point shifts so
// the cutoff climbs. Octave rise vs the effective injection u = expand*env
// (both 0..1), mapped onto the SPICE grid Vexp = 0..0.9 step 0.1 at full
// envelope. expand=1 at full envelope = u_max = 0.7 (Vexp 0.7 -> +2.25 oct;
// the top of the graceful monotone range before the cell runs out of
// control-current headroom). Interpolated like the f0/fh tables; f0 and fh
// (the tracking HPF) rise together, as they do in the netlist (the HPF
// corner tracks the cutoff). The octave DEPTH varies mildly with the base
// operating point in SPICE (more octaves per volt at lower f0); the DSP
// uses the default-op fit, exact at vfc=-6 where the pluck is validated.
expUMax = 0.7;
expOctT = (0.0, 0.016, 0.116, 0.432, 0.903, 1.397, 1.854, 2.250, 2.586, 2.866);
envForExp = ba.if(expForce >= 0.0, expForce, env);   // test hook (see above)
expU = max(0.0, min(0.9, expand * envForExp * expUMax));  // Vexp-equiv, 0..0.9
expPos = expU / 0.1;                                 // grid position, 0..9
expI = min(8, int(expPos));
expOct = it.interpolate_linear(expPos - expI,
    ba.selectn(10, expI, expOctT), ba.selectn(10, expI + 1, expOctT));
expScale = pow(2.0, expOct);
f0m = min(f0 * expScale, 0.9 * ma.SR);
fhm = min(fh * expScale, 0.9 * ma.SR);

// tracking input HPF (C301 against the cutoff-dependent input resistance).
// Base (unmodulated) instances keep their names for dsp/instrument.dsp, which
// deliberately omits the per-key envelope; the channel's own core (below)
// uses the EXPAND-modulated corners f0m/fhm (identical to the base when
// expand=0 or env=0, so all EXPAND-off behavior is bit-for-bit unchanged).
hp1x2 = tf1biq2x(1.0, 0.0, 1.0, 2.0 * ma.PI * fh);
// resonant 2-pole (Sallen-Key core), prewarped at f0
lp2x2 = tf2biq2x(0.0, 0.0, 1.0, 1.0 / qq, 1.0, 2.0 * ma.PI * f0);
// EXPAND-modulated variants used by the channel process
hp1x2m = tf1biq2x(1.0, 0.0, 1.0, 2.0 * ma.PI * fhm);
lp2x2m = tf2biq2x(0.0, 0.0, 1.0, 1.0 / qq, 1.0, 2.0 * ma.PI * f0m);

// --- core nonlinearity (reverse-saturation junction law) ----------------
// offset tanh, normalized to unity small-signal gain:
//   nl(v) = A*(ma.tanh(v/A + c) - ma.tanh(c)) / sech^2(c)
// A (clip scale, volts at the core node) and c (asymmetry) calibrated so
// the H2/H1 vs drive trajectory matches the ngspice transient measurement
// (tests/test_gate_dsp.py::test_large_signal_harmonics_match_spice) at the
// default operating point. The rewired FC interface moved that point from
// f0 ~ 917 Hz to ~1.6 kHz: the control current (= the junctions' linear
// signal budget) grew ~1.7x and A refit 0.0035 -> 0.0095 (5/20/100 mV
// drives: H1 within 1%, H2/H1 within 2..10% of SPICE).
nlA = 0.0095;
nlC = 1.0;
sech2c = 1.0 - ma.tanh(nlC) * ma.tanh(nlC);
nlRaw(v) = nlA * (ma.tanh(v / nlA + nlC) - ma.tanh(nlC)) / sech2c;
nl(v) = ba.if(bypassNl > 0.5, v, nlRaw(v));

// --- channel -------------------------------------------------------------
osc = oscAmp * os.osc(oscFreq);
core(x) = up(x * vca) : hp1x2m : (nl, nl) : lp2x2m : downHB : *(lingain);
process = _ <: ba.if(oscAmp > 1e-9, osc, _) : core;
