// KLM-62 resonator, real-time model.
//
// Mirrors netlists/klm62-resonator.cir:
//   Sallen-Key 47.7 Hz input HPF -> -31.6 dB pad -> three IDENTICAL C-input
//   bandpass stages (f0 = 1/(2*pi*R*sqrt(Cin*Cfb)), Q ~= 5, gain 50; both
//   frequency resistors are the two LDR elements of one P873D vactrol) ->
//   unity inverting summer -> wet/dry blend pot (panel control).
//
// Accuracy features beyond the ideal-stage model:
//   - Bus-loading correction: the pad's Thevenin source resistance
//     Rs = R105||R106 = 26.3 ohm drives the three stage input impedances,
//     which dip to ~R/50 at each resonance, so the real bus voltage is
//     Vbus = Vpad_ideal / (1 + Rs*sum(Yin_i(s))). With
//       Yin(s) = Cin*s*(2*R*Cfb*s + 1) / (R^2*Cin*Cfb*s^2 + 2*R*Cfb*s + 1)
//     (same denominator as the stage), each factor 1/(1 + Rs*Yin_i) is the
//     biquad D_i(s)/D'_i(s) where D' just modifies the s^2 and s^1
//     coefficients. We apply the product of the three per-stage factors to
//     the bus, which matches the exact divider to <0.35 dB worst case
//     (cross terms Rs^2*Yi*Yj are second-order small; validated against
//     ngspice in tests/test_dsp_vs_spice.py).
//   - 2x-oversampled band core: the bus signal is upsampled through a
//     polyphase 63-tap Kaiser(8) halfband, the correction + stage biquads
//     run at 2*SR (bilinear warp error at 48k host was up to ~2 dB for
//     bands near Nyquist), and the summed output is decimated through the
//     same halfband. The 2x stream is represented as (even, odd) sub-sample
//     pairs at host rate; each biquad advances its state twice per tick.
//     Wet-path latency: 31 host samples (the dry path is delayed to match).
//
// Band stagger comes only from the three vactrol LEDs being driven
// differently (RESONATORS 2/2 sheet). Until that sheet's drive law is
// modeled, the three stages get a provisional octave stagger (R, R/2, R/4)
// from one CV; keep in sync with analysis/ac_analysis.py:STAGE_R_SCALE.
// peak1..peak3 (panel PEAK CONT, 1st peak = lowest band) get a provisional
// exponential Rldr scaling hook; the cv-drive-law stream supplies the real
// law (see bandR below).
import("stdfaust.lib");
vc = library("vactrol.dsp");

// --- component values ---
// color variant cap table (0=yellow 1=green 2=blue 3=gray 4=white),
// same caps for all three stages; sync with analysis/ac_analysis.py:COLORS
color = nentry("color", 0, 0, 4, 1);
cin = (0.082e-6, 0.068e-6, 0.056e-6, 0.039e-6, 0.033e-6) : ba.selectn(5, color);
cfb = (820e-12, 680e-12, 560e-12, 390e-12, 330e-12) : ba.selectn(5, color);

// input HPF (C101/C102 0.033u, R102 150k, R103 68k)
// H(s) = s^2/(s^2 + s*2/(R103*C) + 1/(C^2*R102*R103)) -> Q = 0.5*sqrt(R103/R102)
hpfW0 = 2.0 * ma.PI * 47.66;
hpfQ = 0.5 * sqrt(68.0 / 150.0);

pad = 27.0 / 1027.0;             // R105/R106 ideal divider
rsBus = 1000.0 * 27.0 / 1027.0;  // R105 || R106: bus Thevenin source resistance

// --- controls (parameter interface contract) ---
cv = hslider("cv", 0.5, 0.0, 1.0, 0.001);        // sweep bus; 0.5 = 0 V = no modulation
peak1 = hslider("peak1", 0.5, 0.0, 1.0, 0.001);  // lowest band; 0.5 = factory trim
peak2 = hslider("peak2", 0.5, 0.0, 1.0, 0.001);
peak3 = hslider("peak3", 0.5, 0.0, 1.0, 0.001);  // highest band
blend = hslider("blend", 1.0, 0.0, 1.0, 0.001);           // pot k: 0 dry, 1 wet
bypassVactrol = checkbox("bypass_vactrol");               // tests set Rldr directly
rldrDirect = nentry("rldr", 47e3, 1e3, 1e6, 1.0);

// Sweep-bus operating point:
// the RES MOD buses are the panel PEAK FREQ CV jack, printed -5V~+5V, and the
// canonical sweep source (MG2 OUT) is a +/-2.73 V triangle. cv 0..1 maps to
// the BIPOLAR bus, vbus = 10*(cv - 0.5) = -5..+5 V; cv = 0.5 <=> 0 V bus
// (nothing patched) where the FC trims anchor the factory center frequencies
// (Rldr = 47k for band 1). MG2 full drive spans cv ~= 0.227..0.773.
vbus = 10.0 * (cv - 0.5);
// fitted KLM-62D law (analysis/cv_law.json): log2(Rldr) affine in bus volts,
// slope -octPerVolt oct/V, anchored at 47k / 0 V; SPICE-refereed by
// tests/test_cv_law.py. The vactrol dynamics stage takes the resistance
// target directly (law/dynamics split, dsp/vactrol.dsp).
rldrTarget = 47e3 * pow(2.0, 0.0 - vbus * octPerVolt);
rldrBase = ba.if(bypassVactrol, rldrDirect, vc.vactrolR(rldrTarget));

// KLM-62D CV law (netlists/klm62-cv.cir, analysis/cv_law.json): matrix inputs
// sum linearly into one expo converter, so per-band peak/trim CVs are exact
// multiplicative factors on Rldr. 0.425 oct/V at any 270k input (= 0.8
// photocell slope / 1.87 V-per-oct of LED current); the FC trims give the
// 1-octave factory stagger (R, R/2, R/4 at peak = 0.5, which is what
// analysis/ac_analysis.py's reference uses).
octPerVolt = 0.425;
vpk(p) = 10.0 * zb / (rp * (1.0 - p) + zb)   // loaded wiper: 10k pot into 10k||270k
with {
    rp = 10e3;
    rload = 1.0 / (1.0 / 10e3 + 1.0 / 270e3);
    zb = 1.0 / (1.0 / max(rp * p, 1.0) + 1.0 / rload);
};
vpkRef = vpk(0.5);   // ~3.97 V: peak default, where band scales are (1, 1/2, 1/4)
// clamp mirrors the netlist photocell's validated range (R in [1k, 1M])
bandR(oct, p) = min(1e6, max(1e3,
    rldrBase * pow(2.0, oct - (vpk(p) - vpkRef) * octPerVolt)));
r1 = bandR(0.0, peak1);
r2 = bandR(-1.0, peak2);
r3 = bandR(-2.0, peak3);

// --- 2x-oversampled band core -------------------------------------------
// Internal rate for the correction + stage biquads. The 2x stream is a pair
// of host-rate signals (even, odd sub-sample).
sr2 = 2.0 * ma.SR;

// bilinear transform of the w1-normalized analog prototype
//   H(S) = (b2 S^2 + b1 S + b0)/(S^2 + a1 S + a0),  S = s/w1
// at sample rate sr2, prewarped so analog w1 lands exactly on digital w1
// (same math as fi.tf2s, which is hardwired to ma.SR), feeding the
// double-stepped biquad below.
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

// transposed-direct-form-II biquad advancing two sub-samples per host tick:
// inputs (xe, xo) = even/odd sub-samples, outputs (ye, yo). The feedback pair
// carries the filter state across host ticks.
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

// per-stage frequency-normalized analog coefficients (shared by stage and
// its loading correction); w0 clamped below the INTERNAL Nyquist: the
// hardware happily sweeps f0 out of the audio band, but bilinear prewarp
// flips sign past sr2/2 -> unstable. Clamped bands sit above the host
// audio band and are removed by the decimation halfband, like the hardware's
// bands simply leaving the audible range.
w0of(r) = min(1.0 / (r * sqrt(cin * cfb)), 2.0 * ma.PI * 0.45 * sr2);

// one stage: H(s) = -(s/(R*Cfb)) / (s^2 + s*2/(R*Cin) + 1/(R^2*Cin*Cfb))
band2x(r) = tf2biq2x(0.0, b1n, 0.0, a1n, 1.0, w0)
with {
    w0 = w0of(r);
    b1n = (0.0 - 1.0 / (r * cfb)) / w0;
    a1n = (2.0 / (r * cin)) / w0;
};

// loading correction factor for one stage: 1/(1 + Rs*Yin(s)) = D(s)/D'(s),
//   D'(s) = s^2*(1 + 2Rs/R) + s*(2/(R*Cin) + Rs/(R^2*Cfb)) + w0^2
// normalized to w0 and made monic (divide through by g = 1 + 2Rs/R)
corr2x(r) = tf2biq2x(1.0 / g, a1n / g, 1.0 / g, a1pn / g, 1.0 / g, w0)
with {
    w0 = w0of(r);
    g = 1.0 + 2.0 * rsBus / r;
    a1n = (2.0 / (r * cin)) / w0;
    a1pn = (2.0 / (r * cin) + rsBus / (r * r * cfb)) / w0;
};

// polyphase halfband resampling (63-tap, exact halfband: center 0.5, even
// offsets zero; odd-offset windowed sinc, Kaiser beta=8, side taps scaled
// for exactly unity DC gain -> 0.001 dB passband ripple to 20 kHz at
// fs=48k host, -80 dB stopband). hbe holds the interpolator's even-branch
// taps 2*h[2j], j=0..31; the decimator reuses them as h[2j] = hbe/2.
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
up(x) = firHbe(x), (x @ 15);                        // 1 in -> (even, odd)
downHB(ue, uo) = 0.5 * firHbe(ue) + 0.5 * (uo @ 16); // (even, odd) -> 1 out
lat = 31;  // up + down group delay in host samples (dry path matches)

// Sallen-Key HPF as normalized tf2s: H(s) = s^2 / (s^2 + s*w0/Q + w0^2)
// (runs at host rate: 47.7 Hz corner has negligible warp)
buffer = fi.tf2s(1.0, 0.0, 0.0, 1.0 / hpfQ, 1.0, hpfW0);

// summer is inverting; wet/dry blend models the external pot (dry taken
// from buf_out, before the pad, per the schematic), dry leg delayed to
// stay time-aligned with the oversampled wet path
// si.interpolate(i, x0, x1): i=0 -> x0 (dry), i=1 -> x1 (wet)
resonate(x) = (x @ lat), wet(x) : si.interpolate(blend)
with {
    wet(b) = 0.0 - (core : downHB)
    with {
        core = up(b * pad) : corr2x(r1) : corr2x(r2) : corr2x(r3)
            <: band2x(r1), band2x(r2), band2x(r3) :> _, _;
    };
};

process = buffer : resonate;
