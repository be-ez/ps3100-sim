// KLM-63 MOD-VCA + MG2 (PS3100/PS3300), real-time model.
//
// Mirrors netlists/klm63-modvca-mg2.cir:
//   - MOD-VCA: IC21a inverts by -R202/R201 = -0.75, the F201 vactrol
//     photocell + R204 2.2k feed IC21b's virtual ground against VR201 10k,
//     so the audio path is a flat (no caps on the sheet) gain
//         g = +0.75 * VR201 / (Rldr + 2.2k)
//     from ~-42 dB (dark, Rldr 1M) to ~+2.8 dB (lit, Rldr ~3.2k).
//   - LED drive (Q201): MOD VCA CONT panel pot (100k across +10V/G3) ->
//     R206 10k -> base (R207 100k + D201 clamp); emitter on R210 1k // R209
//     33k to -14.9V (a ~0.44 V turn-on offset), LED from +14.9V through
//     R208 1k into the collector. Rather than a fitted curve, the drive law
//     below solves the same physics as the SPICE deck (exponential BJT
//     emitter node via unrolled Newton in Lambert-W form, base loading,
//     Early effect, LED forward drop, saturation of Q201 near the top of
//     the pot) with the same device constants as the netlist's model cards;
//     it matches the ngspice DC sweep within ~0.1 dB of VCA gain over the
//     full pot travel (tests/test_modvca_dsp.py referees against fresh
//     SPICE runs). All iterations are routed as (value, ...) pairs through
//     seq() so the expression tree stays shared (a closed-over unrolled
//     Newton blows up Faust's box evaluation).
//   - Photocell law (shared with the netlist and netlists/klm62-cv.cir):
//     R = 15k * (I_led/1mA)^-0.8, clamped to [1k, 1M].
//   - Vactrol dynamics: the SPICE deck is static (DC/AC referee); at audio
//     rate the F201 resistance goes through dsp/vactrol.dsp's two-population
//     power-balanced model via its resistance-target entry point
//     vactrolR(rTarget), which settles to R with the P873's asymmetric
//     attack/decay dynamics. The law/dynamics split is deliberate: board
//     drive laws live per-board (the Q201 solve above); the dynamics are
//     shared in vactrol.dsp (its cv2r is the KLM-62D law only).
//   - MG2: triangle-core LFO (integrator IC22a + Schmitt IC22b + Q202).
//     Modeled as an ideal asymmetric triangle at the physics-derived rate:
//     thresholds +/-Vsat*51/151, slopes (nA/2)/(R213*C201) falling and
//     ((nA/2 - Vce202)/R215 - (nA/2)/R213)/C201 rising, where nA is the
//     rate node solved from the FREQ CONT pot / R211 / R212 network.
//     Matches SPICE transient frequency within ~1% over the pot
//     (~0.3..12 Hz), amplitude +/-2.73 V at pin 27. R215 sits on the
//     integrator summing node (confirmed by full-res scan re-read; an
//     earlier "+ input" transcription was a misread -- see netlist header).
//
// Controls:
//   vca_cv   0..1  MOD VCA CONT panel pot position
//   mg2_rate 0..1  FREQ CONT panel pot position
//   bypass_vactrol / rldr : test hooks, set the photocell resistance
//     directly (same contract as dsp/resonator.dsp)
//   probe    0..3  test hook for the offline driver (prints channel 0 only):
//     0 = VCA audio out, 1 = static-law R/rmax, 2 = dynamic R/rmax,
//     3 = MG2 output (volts at pin 27)
// process: 1 audio in -> (channel0 per probe, MG2 volts at pin 27)
import("stdfaust.lib");
vc = library("vactrol.dsp");

// --- controls ---
vcaCv = hslider("vca_cv", 0.5, 0.0, 1.0, 0.001);
mg2Rate = hslider("mg2_rate", 0.5, 0.0, 1.0, 0.001);
bypassVactrol = checkbox("bypass_vactrol");
rldrDirect = nentry("rldr", 47e3, 1e3, 1e6, 1.0);
probe = nentry("probe", 0, 0, 3, 1);

// --- device constants (sync with netlists/klm63-modvca-mg2.cir models) ---
vt = 0.02585;        // kT/q at ~27C
isQ = 1e-14;         // QC945 IS=10f
betaQ = 300.0;       // QC945 BF
vaf = 100.0;         // QC945 VAF (Early)
nvtLed = 1.8 * vt;   // LEDP873 N=1.8
isLed = 1e-18;       // LEDP873 IS
rsLed = 1.0;         // LEDP873 RS
vceSat = 0.096;      // Q201 deep-saturation Vce (measured in the deck)
vsup = 14.9;

// --- Q201 emitter network: R210 1k // R209 33k to -14.9V ---
ge = 1.0 / 1e3 + 1.0 / 33e3;   // emitter load conductance
veOff = 14.9 / 33e3 / ge;      // 0.438 V: R209's turn-on offset
r206 = 10e3;
r207 = 100e3;

// LED forward drop at current i
vfLed(i) = nvtLed * log(max(i, 1e-12) / isLed) + i * rsLed;

// Exact emitter-node solve: ie = IS_eff*exp((vb-ve)/vt), ie = (ve+veOff)*ge.
// Lambert-W form, unrolled Newton in u = ln(ie):
//   g(u) = u - lnA + b*e^u, lnA = ln(IS_eff) + (vb+veOff)/vt, b = 1/(ge*vt);
// g is convex and increasing, so Newton converges globally (8 steps).
emitterCurrent(vb, isEff) = exp(u8)
with {
    lnA = log(isEff) + (vb + veOff) / vt;
    b = 1.0 / (ge * vt);
    nstepP(u, l) = (t * (u - 1.0) + l) / (1.0 + t), l with { t = b * exp(u); };
    u8 = (log(1e-3), lnA) : seq(i, 8, nstepP) : (_, !);
};

// MOD VCA CONT pot position -> F201 photocell resistance (static law).
// Mirrors the SPICE deck: fixed-point passes of {emitter solve, Early
// factor, LED drop, base loading}, then the Q201-saturated branch
// (vc = ve + vceSat, ie = ic + ib, linear in vb), iled = min of both.
rTargetOf(p) = rr
with {
    vth = 10.0 * p;
    rth = 100e3 * p * (1.0 - p) + r206;   // pot Thevenin + R206
    gth = 1.0 / rth;
    passP(vb, vce) = vbNew, vceNew
    with {
        early = 1.0 + vce / vaf;
        ie = emitterCurrent(vb, isQ * (early + 1.0 / betaQ));
        ic = ie * early / (early + 1.0 / betaQ);
        vceNew = max(vsup - vfLed(ic) - ic * 1e3 - (ie / ge - veOff), vceSat);
        vbNew = (vth * gth - (ie - ic)) / (gth + 1.0 / r207);
    };
    vb2 = ((vth * r207 / (rth + r207)), 14.0) : seq(i, 2, passP) : (_, !);
    vce2 = ((vth * r207 / (rth + r207)), 14.0) : seq(i, 2, passP) : (!, _);
    early2 = 1.0 + vce2 / vaf;
    ieAct = emitterCurrent(vb2, isQ * (early2 + 1.0 / betaQ));
    icAct = ieAct * early2 / (early2 + 1.0 / betaQ);
    vbe = vt * log(max(ieAct, 1e-15) / isQ);
    den = ge + gth + 1.0 / r207 + 1e-3;
    vbsOf(vf) = ((vsup - vf + vbe - vceSat) * 1e-3 + vth * gth
        + (vbe - veOff) * ge) / den;
    icSatOf(vf) = (vsup - vf - vbsOf(vf) + vbe - vceSat) * 1e-3;
    icS1 = icSatOf(vfLed(icAct));
    icS2 = icSatOf(vfLed(max(icS1, 1e-12)));
    icS3 = icSatOf(vfLed(max(icS2, 1e-12)));
    iled = min(icAct, max(icS3, 0.0));
    // P873-class CdS power law, shared with the netlists
    rr = min(1e6, max(1e3, 15e3 * pow(max(iled, 1e-12) * 1e3, -0.8)));
};

// --- VCA: static target R -> vactrol dynamics -> flat gain ---
rStatic = rTargetOf(vcaCv);
// direct resistance-target dynamics: settles to rStatic
rDyn = ba.if(bypassVactrol, rldrDirect, vc.vactrolR(rStatic));
vcaGain = 0.75 * vr201 / (rDyn + 2.2e3)
with {
    vr201 = 10e3;   // trimmer assumed full scale (netlist header)
};

// --- MG2 triangle LFO ---
mg2Out = th * (1.5 / 2.5) * triNorm   // R221/R222 pad to pin 27
with {
    vsatOp = 13.5;                    // 4558 swing on +/-14.9V rails
    vce202 = 0.019;                   // Q202 deep-sat Vce (measured in deck)
    c201 = 0.022e-6;
    th = vsatOp * 51.0 / 151.0;       // Schmitt thresholds (R217/R218)
    // rate node nA: solve the (v26, nA) 2-node network exactly.
    // nA sees R213 -> nInt (= nA/2) and R214+R216 -> ground: constant
    // conductance kA; R211 15k to the wiper, R212 1M to +14.9V.
    rtop = max(10e3 * (1.0 - mg2Rate), 1.0);
    rbot = max(10e3 * mg2Rate, 1.0);
    g26 = 1.0 / rtop + 1.0 / rbot + 1.0 / 15e3;
    kA = (1.0 / 1e6 + 1.0 / 510e3) / 2.0;
    gA = 1.0 / 15e3 + 1.0 / 1e6 + kA;
    det = g26 * gA - (1.0 / 15e3) * (1.0 / 15e3);
    nA = (g26 * 14.9 / 1e6 + (10.0 / rtop) / 15e3) / det;
    // integrator slopes: Q202 off -> R213 charges (falling); Q202 on ->
    // R215 sinks about twice that (rising)
    sd = (nA / 2.0) / 1e6 / c201;                          // V/s falling
    su = ((nA / 2.0 - vce202) / 510e3) / c201 - sd;        // V/s rising
    freq = 1.0 / (2.0 * th * (1.0 / su + 1.0 / sd));
    dutyUp = (1.0 / su) / (1.0 / su + 1.0 / sd);
    ph = os.lf_sawpos(freq);
    triNorm = ba.if(ph < dutyUp,
        -1.0 + 2.0 * ph / dutyUp,
        1.0 - 2.0 * (ph - dutyUp) / (1.0 - dutyUp));
};

// --- outputs ---
process(x) = ch0, mg2Out
with {
    ch0 = (x * vcaGain, rStatic / vc.rmax, rDyn / vc.rmax, mg2Out)
        : ba.selectn(4, probe);
};
