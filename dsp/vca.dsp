// KLM-76 VCA / Phone Amp (PS3100), real-time model.
//
// Mirrors netlists/klm76-vca.cir: two P873 vactrol VCAs in series (VCA1
// pin 37->38 normalled into VCA2 pin 39->35) plus the headphone amp.
//
// Per VCA (values VCA1/VCA2 = R301/R310 22k, R302/R326 3.3k, C301/C302 1u,
// Rf = 22k + trim):
//   H(s) = -(R302/(R301+R302)) * Rf / (Rpre + Rldr + 1/(s*C))
//        = -kdiv*Rf/(Rpre+Rldr) * s/(s + 1/((Rpre+Rldr)*C)),  Rpre = R301||R302
//   i.e. a CV-dependent gain with a one-pole HPF whose corner slides from
//   ~21 Hz (bright) to sub-Hz (dark). VCA1's output loads into VCA2's input
//   network through R309 470 (kload below); VCA2's out (R317 220, pin 35) is
//   modeled unloaded. CF302 47p across VCA2's feedback is OMITTED here: its
//   corner sits at 1/(2*pi*52.1k*47p) ~= 65 kHz at the default trim (-0.39 dB
//   at 20 kHz, -0.10 dB at 10 kHz), above the internal Nyquist; at full trim
//   (Rf=122k, 28 kHz) it would shade the top octave - deferred.
//
// CV -> Rldr static law: 41-point log2(Rldr) table at 0.15 V steps over
// 0..6 V - the REAL CV range (panel jacks 0V~+5V image-verified, GEG OUT2
// sustain +5.87 V; the old 0..10 V table wasted half its span on unreachable
// drive) - sampled from the SPICE DC sweep of the LED driver (Q301 receiver
// R303/D301/R305; R304 taps the node ABOVE D301 per the 2026-07-21 full-res
// re-read, so the base rides one Vf above the CV - a VBE-compensating shift
// that puts the turn-on at CV ~= -0.05 V, i.e. the LED idles at ~14 uA even
// at CV = 0; diode-chain emitter network R308 || D303-D306+R324, LED cap
// R307, P873 photocell power law R = 15k*(I/1mA)^-0.8 clamped [1k,1M] - same
// law as netlists/klm62-cv.cir). Regenerate: run the netlist's `dc VC1 0 6`
// sweep and take log2(v(rmon1)) every 0.15 V (tests/test_vca_dsp.py rechecks
// the table against a fresh sweep). Table interp error <=0.10 dB per stage
// for CV >= 0.3 V (<=0.05 dB above 1 V); larger (<=0.75 dB) only below
// 0.3 V in the steep climb out of the dark idle, where chain gain is
// < -70 dB.
//
// Dynamics: the two-population vactrol model (dsp/vactrol.dsp) is REUSED via
// its resistance-target entry point vactrolR(rTarget). The law/dynamics split
// is deliberate: board drive laws live per-board (the KLM-76 table above);
// the P873 dynamics are shared in vactrol.dsp (its cv2r is KLM-62D only).
import("stdfaust.lib");
vac = library("vactrol.dsp");

// --- controls ---
// cv1/cv2: VCA1/VCA2 CONT (pins 36/34), 0..1 -> 0..5 V panel scale.
// FULL-SCALE CHANGE (cross-board reconciliation 2026-07-21): the panel VCA
// CV jacks are printed 0V~+5V (image-verified, scan p0023), not the 0..10 V
// first assumed - cv=1.0 now means +5 V at the pin. VCA1 CONT is GEG OUT 2
// (0..+5.87 V trapezoid) or an external jack; VCA2 CONT the Voltage
// Processor out or a jack. An
// unpatched pin floats up through R303/D301 to ~5 V ("normalled on") - i.e.
// essentially FULL SCALE, cv ~= 1.0, not mid-range; patched sources drive
// the pin directly, which is what cv1/cv2 model.
cv1 = hslider("cv1", 1.0, 0.0, 1.0, 0.001);
cv2 = hslider("cv2", 1.0, 0.0, 1.0, 0.001);
bypassVactrol = checkbox("bypass_vactrol");  // 1: skip dynamics (static law)
rldr1Direct = nentry("rldr1", 0, 0, 1e6, 1); // test hooks: force Rldr, 0 = off
rldr2Direct = nentry("rldr2", 0, 0, 1e6, 1);
monitor = nentry("monitor", 0, 0, 1, 1);     // 0: VCA2 out (pin 35), 1: phone amp

// --- static CV law (log2 Rldr vs CV volts, 0..6 V in 0.15 V steps) ---
cvTable = (
    18.837037, 17.606021, 16.974730, 16.556955,
    16.246286, 15.999550, 15.795166, 15.620845,
    15.468947, 15.334394, 15.212419, 15.103020,
    15.002483, 14.908115, 14.814150, 14.704317,
    14.546655, 14.316091, 14.047331, 13.784492,
    13.546484, 13.335984, 13.150228, 12.985415,
    12.838045, 12.705205, 12.583327, 12.473172,
    12.371769, 12.277910, 12.190609, 12.109050,
    12.032558, 11.960565, 11.892592, 11.828228,
    11.767124, 11.708977, 11.653522, 11.600531,
    11.549800
);
rStatic(v) = pow(2.0, ba.listInterp(cvTable, max(0.0, min(6.0, v)) / 0.15));

// vactrol dynamics around the static target (direct resistance entry point)
rOf(cvn, direct) = ba.if(direct > 0.5, direct,
                         ba.if(bypassVactrol, rT, vac.vactrolR(rT)))
with {
    rT = rStatic(5.0 * cvn);
};
r1 = rOf(cv1, rldr1Direct);
r2 = rOf(cv2, rldr2Direct);

// --- audio path constants (netlist designators) ---
kdiv = 3.3 / 25.3;               // R302/(R301+R302) input divider
rpre = 22.0e3 * 3.3e3 / 25.3e3;  // R301 || R302 = 2869.6
c301 = 1.0e-6;                   // C301/C302 DC blocks
// Rf = R306 + VR301 (trim default 0.301 = unity chain gain at the REAL full
// drive, CV = +5 V panel max -> Rldr = 3922; recalibrated 2026-07-21 from
// the old CV=10 V point, which left the chain ~5.6 dB under unity at the
// real max. GEG sustain +5.87 V overdrives to +2.1 dB. Matches the
// netlist's calibrated vr1/vr2 - keep in sync)
rf = 22.0e3 + 0.301 * 100.0e3;

// one-pole HPF, bilinear with prewarped corner (corner <= 21 Hz: warp nil)
hpf1(wc, x) = (rec ~ _)
with {
    t = tan(wc * 0.5 / ma.SR);
    rec(y) = ((x - x') + (1.0 - t) * y) / (1.0 + t);
};

// one VCA: inverting gain + sliding HPF (see header)
vcaStage(r, x) = hpf1(wc, x) * g
with {
    g = 0.0 - kdiv * rf / (rpre + r);
    wc = 1.0 / ((rpre + r) * c301);
};

// VCA1 -> VCA2 inter-stage loading: R309 470 into VCA2's input impedance
// Zin = R310 + R326||Rldr2 (midband; C302's reactance is negligible next to
// the 22k, <0.02 dB shading)
kload = zin / (470.0 + zin)
with {
    zin = 22.0e3 + 3.3e3 * r2 / (3.3e3 + r2);
};

// phone amp (IC32 + Q303/Q304 follower): non-inverting amp, R331 10k into
// the + input, feedback R333 27k with R332 10k shunt on the - input ->
// gain 1 + 27k/10k = 3.7 (+11.4 dB); swing clipped at the 4558/follower
// rails. (Full-res scan re-read 2026-07-21: R332 sits on the inverting
// node, not across the + input - the old x0.5 divider reading was wrong.)
// In the instrument it is fed from VCA2 out through the FINAL VOLUME and
// PHONE LEVEL panel pots (values not on this sheet); modeled standalone
// from the module input, matching the netlist's separate VPH source.
phoneAmp(x) = max(-13.4, min(13.4, 3.7 * x));

process = _ <: (vcaStage(r1) * kload : vcaStage(r2)), phoneAmp
    : ba.selectn(2, monitor);
