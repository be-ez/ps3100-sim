// KLM-63 MG1 + noise generator (PS3100/PS3300), real-time model.
//
// Mirrors netlists/klm63-mg1-noise.cir. SPICE-refereed in
// tests/test_mg1_noise_dsp.py:
//   - LFO frequency + waveform levels vs ngspice transient at matched
//     settings (free-running oscillator: rate/shape compared, not phase)
//   - pink-filter magnitude response vs ngspice AC of the shaping network
//
// MG1 (LFO):
//   rate law (netlist hand theory, SPICE-verified to ~0.5%):
//     Vcv = (vfc1/R301 + vfc2/R302 + 14.9/R304 - 14.9*fadj/Radj) / Gtot
//           with Radj = R303 + wiper impedance of VR301 = 470k + 100k*a*(1-a)
//     Ic  = I_ref * exp(Vcv/VT),  I_ref = 14.9V/R305 = 1.49 uA
//     f   = Ic / (4 * C302 * Vth),  Vth = Vsat*R327/RHYS = 13*10/35.75
//         = 3.636 V (RHYS panel-anchored to the 5VP-P MG 1 OUT print:
//         pin 34 swings +/-2.5 V = 5 Vpp; see the netlist header)
//   waveforms (all levels from the netlist build-out dividers):
//     pin 34 triangle       = 0.6875 * tri_core        (R314/R315)
//     pin 35 inverted tri   = -pin 34                  (IC33, R318/R319)
//     pin 36 square         = 0.18478 * (+/-13 V)      (R328/R329)
//     pin 37 "sine"         = 0.6875 * shaper(tri)     (R323/R324)
//   The sine shaper wiring is CONFIRMED by the full-res scan re-read
//   (D307/D308 rectify the two complementary triangles into IC34a): a
//   diode-rounded rectified triangle at 2x the LFO rate is the genuine
//   pin-37 output.
//
// NOISE:
//   The 2SC644 avalanche junction is BEHAVIORAL here (no.noise, uniform,
//   scaled to the 2 mV rms the SPICE deck injects; the real junction is
//   gaussian-ish - spectrum shaping, not the PDF, is what is validated).
//   White (full-res-confirmed topology): IC35a non-inverting with gain
//   1 + 1M/(4.7k + VR304) - VR304 "NOISE GAIN" is a 47k rheostat in the
//   gain leg - then IC35b (-1M/82k); midband gain law mirrored exactly.
//   The gain-leg C310 shelf (<= 3.4 Hz) and C311 highpass (0.19 Hz) are
//   below any audio concern and omitted.
//   Pink: the exact s-domain transfer of the IC31b stage,
//     H(s) = -C312*(1+sT1)(1+sT2)(1+sT3) / (N(s)*(1+s*R337*C312)),
//     T1 = R338*C315 = 0.15 ms, T2 = R339*C316 = 1.5 ms, T3 = R340*C317 =
//     150 ms, N(s) the cubic admittance numerator of the feedback ladder,
//   factored into
//   three matched-z pole-zero shelves + one anchor-fitted lone pole (see the
//   realization note below). C315 has NO value printed on the sheet
//   (confirmed at full res); 1.5 nF assumed.
//
// Controls (this board's interface; panel wiring of the FREQ CONT pots is
// cross-board and unresolved, so the pins are exposed in volts):
//   vfc1, vfc2 : FREQ CONT I / II pin voltages (V). ~0.59 V/oct via vfc1.
//   fadj       : FREQ ADJ trimmer 0..1 (1 = slowest). 0.5 -> ~7.4 Hz.
//   noise_gain : VR304 rheostat position 0..1 (1 = 0 ohm, loudest; the
//                first-stage gain trim per the full-res re-read).
//   outsel     : 0 tri (pin 34), 1 inv tri (35), 2 square (36), 3 sine (37),
//                4 white (41), 5 pink (42),
//                6 input through the pink filter   (test hook, SPICE referee)
//                7 input through the white-path gain (test hook)
import("stdfaust.lib");

// --- controls ---
vfc1 = hslider("vfc1", 0.0, -15.0, 15.0, 0.01);
vfc2 = hslider("vfc2", 0.0, -15.0, 15.0, 0.01);
fadj = hslider("fadj", 0.5, 0.0, 1.0, 0.001);
ngain = hslider("noise_gain", 0.5, 0.0, 1.0, 0.001);
outsel = nentry("outsel", 0, 0, 7, 1);

// --- MG1 rate law (constants mirror the netlist; see header) ---
vt = 0.025865;            // kT/q at ngspice's 27 C default
iref = 14.9 / 10.0e6;     // R305 10M from +14.9V
radj = 470.0e3 + 100.0e3 * fadj * (1.0 - fadj);  // R303 + VR301 wiper Z
gtot = 1.0/1.8e3 + 1.0/56.0e3 + 1.0/100.0e3 + 1.0/radj + 1.0/330.0e3;
vcv = (vfc1/56.0e3 + vfc2/100.0e3 + 14.9/330.0e3 - 14.9*fadj/radj) / gtot;
vth = 13.0 * 10.0 / 35.75; // Schmitt threshold (RHYS panel-anchored to the
                           // 5VP-P MG 1 OUT print -> pin 34 5 Vpp, see netlist)
c302 = 0.1e-6;
// one-step base-current correction: Q301's Ib = Ic/beta loads the ~1.7k
// Thevenin CV divider (-2.4% at ~110 uA; beta 300 per the SPICE model)
ic0 = iref * exp(vcv / vt);
ic = ic0 * exp(0.0 - ic0 / 300.0 / gtot / vt);
// clamp: the hardware happily runs into audio rates, but the phase-accum
// square/triangle need f << SR; 2 kHz is far beyond the LFO's panel range
freq = min(ic / (4.0 * c302 * vth), 2000.0);

// --- MG1 core and waveforms ---
ph = (+(freq / ma.SR) ~ ma.frac);          // free-running phase
tri = vth * (4.0 * abs(ph - 0.5) - 1.0);   // falls for ph<0.5 (netlist start)
sqc = 13.0 * (2.0 * (ph < 0.5) - 1.0);     // +13 V while tri falls (IC34b)
outdiv = 2.2 / 3.2;                        // 1k / 2.2k build-out dividers
p34 = outdiv * tri;
p35 = 0.0 - p34;
p36 = (680.0 / 3680.0) * sqc;
// sine shaper as read: |tri| through the diode knees into -220k/100k, plus
// the R325 750k bias from -14.9V. vd = 0.574 V fits the SPICE deck's
// rectified minimum to <0.2% (DSIG knee at the ~31 uA peak diode current
// plus the shaper's residual rounding; re-measured at the 5 Vpp anchor)
vd = 0.574;
dio(v) = max(v - vd, 0.0);
p37 = outdiv * (14.9 / 750.0e3 * 220.0e3 - 2.2 * (dio(tri) + dio(0.0 - tri)));

// --- noise: white path ---
// midband gain, full-res-confirmed topology: IC35a non-inverting
// (1 + R332/(R333 + VR304 rheostat)) then IC35b (-1M/82k)
aWhite = (1.0 + 1.0e6 / (4.7e3 + rng)) * (1.0e6 / 82.0e3)
with {
    rng = 47.0e3 * (1.0 - ngain);
};
jnoise = no.noise * 2.0e-3;   // behavioral junction, ~2 mV scale (see header)
white = jnoise * aWhite;

// --- noise: pink filter (exact factored transfer, provenance in header) ---
// zeros (rad/s): 1/T3, 1/T2, 1/T1 = 6.66667, 666.667, 66666.7
// poles (rad/s): 1/(R337*C312) = 10 exactly, plus the roots of N(s):
//   58.12293, 4163.921, 137117.96 
// gain G = DCgain / prod(z_i/p_i) = 6666.667; sign: IC31b inverts.
//
// Digital realization: matched-z shelves (both corners exactly placed at
// any SR, DC-exact gain) plus the lone 10 rad/s pole as a one-pole with a
// FITTED numerator zero beta. Plain bilinear (fi.tf1s) forces a Nyquist
// zero onto this relative-degree-1 transfer and costs -2.0 dB at 10 kHz /
// -7.7 dB at 20 kHz at fs=48k; beta is instead solved at init so the
// cascade is magnitude-exact at fa (the shelves' residual folded in via
// pinkCorr) as well as at DC. Cascade-vs-analog error over 20 Hz..20 kHz:
// max 0.28 dB at 48k, 0.29 dB at 44.1k, 0.01 dB at 96k (model doc).
pinkZ1 = 6.6666667;   pinkP1 = 58.12292659;
pinkZ2 = 666.66667;   pinkP2 = 4163.920602;
pinkZ3 = 66666.667;   pinkP3 = 137117.9565;
pinkP0 = 10.0;
pinkG = 6666.6667;
pinkFa = min(20000.0, 0.42 * ma.SR);          // magnitude anchor frequency
pinkTha = 2.0 * ma.PI * pinkFa / ma.SR;
pinkWa = 2.0 * ma.PI * pinkFa;
zmag(rho) = sqrt((1.0 - rho * cos(pinkTha)) * (1.0 - rho * cos(pinkTha))
    + rho * rho * sin(pinkTha) * sin(pinkTha));  // |1 - rho*e^{-j tha}|
mztShelf(z, p) = fi.tf1(g, 0.0 - g * zeta, 0.0 - rho)
with {
    zeta = exp(0.0 - z / ma.SR);
    rho = exp(0.0 - p / ma.SR);
    g = (z / p) * (1.0 - rho) / (1.0 - zeta);
};
shelfCorr(z, p) = (sqrt(pinkWa * pinkWa + z * z) / sqrt(pinkWa * pinkWa + p * p))
    / (g * zmag(zeta) / zmag(rho))               // analog/digital ratio at fa
with {
    zeta = exp(0.0 - z / ma.SR);
    rho = exp(0.0 - p / ma.SR);
    g = (z / p) * (1.0 - rho) / (1.0 - zeta);
};
pinkCorr = shelfCorr(pinkZ1, pinkP1) * shelfCorr(pinkZ2, pinkP2)
    * shelfCorr(pinkZ3, pinkP3);
fitPole(p) = fi.tf1(g, g * beta, 0.0 - r)
with {
    r = exp(0.0 - p / ma.SR);
    target = p / sqrt(pinkWa * pinkWa + p * p) * pinkCorr;
    k = target * zmag(r) / (1.0 - r);
    a = 1.0 - k * k;
    b = cos(pinkTha) - k * k;
    beta = (0.0 - b - sqrt(max(b * b - a * a, 0.0))) / a;  // |beta| < 1 root
    g = (1.0 - r) / (1.0 + beta);
};
pinkFilter = *(pinkG)
    : mztShelf(pinkZ1, pinkP1)
    : mztShelf(pinkZ2, pinkP2)
    : mztShelf(pinkZ3, pinkP3)
    : fitPole(pinkP0)
    : *(-1.0);
pink = white : pinkFilter;

process(x) = (p34, p35, p36, p37, white, pink, pinkFilter(x), aWhite * x)
    : ba.selectn(8, outsel);
