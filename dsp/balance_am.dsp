// KLM-62D balance mixer + amplitude modulator, real-time model.
//
// Mirrors netlists/klm62d-balance-am.cir:
//   two inverting keyboard-bus mixers (gain -22 upper / -18.33 lower) ->
//   external KBD VOLUME BALANCE pot -> wiper = AM SIG IN -> /69 attenuator
//   -> 2SK30-GR JFET variable resistor -> inverting stage (-R304/Rds) ->
//   ring-balance summer (VR301 divider, full-res re-read 2026-07-21: dry
//   top via R303, wet bottom via R305, wiper into the IC31b summing node;
//   dry feed R303+47k*rbal subtracts the un-modulated carrier against the
//   wet feed R305+47k*(1-rbal)) -> AM INTENSITY rheostat (confirmed wiring:
//   pin 24 dry node -> pin 26) blending dry wiper signal onto the output
//   node.
//
// Model structure (validated against ngspice in tests/test_balance_am_dsp.py):
//   - The resistive core plus the three significant capacitors (C201/C203
//     balance-leg coupling, C301 JFET output coupling) is solved EXACTLY per
//     sample with SPICE-style trapezoidal companion models: each cap becomes
//     a resistor rc = 1/(2*C*fs) in series with a history EMF h (updated
//     h += 2*rc*i), and the remaining linear network is reduced by hand to
//     one division. This captures the in-band LF corners the caps create
//     when the balance pot slams one leg (72 Hz!), and the positive-feedback
//     loop through the INTENSITY pot (out26 -> RINT -> wiper) whose pole the
//     caps set. NOTE the real loop is RHP-unstable for under-pinched bias
//     at low intensity (e.g. bias<~0.14 at intensity=1, up to ~0.38 as
//     intensity->0); the model diverges there just like the hardware would
//     latch up.
//   - JFET: triode-region 2SK30 law. Static conductance 2*BETA*(Vgs-VTO),
//     plus the channel-voltage term of the Shichman-Hodges triode equation
//     g = BETA*(2*(Vgs-VTO) - Vds) applied by two fixed-point iterations per
//     sample ("corr" hook; this is what makes carrier level and 2nd-order
//     sidebands track SPICE). Saturation region is not modeled: valid for
//     |Vds| below the pinch overdrive, which the /69 input attenuator
//     guarantees at hardware levels.
//   - Modulation path: mod in -> LEVEL pot -> C303/R308 (corner 0.17 Hz,
//     treated as unity) -> gate divider R308:R309 (= x0.5) -> one-pole
//     890 Hz lowpass from the gate node capacitance (C302 68p + JFET
//     junction caps ~8p against R308||R309 = 2.35M). Without this pole the
//     sidebands run ~0.2 dB hot at fm = 200 Hz.
//   Omitted (validated <0.1 dB or off-band): C202 bias bypass (DC only),
//   C303 HP corner 0.17 Hz, the C302/VR304 CANCEL feed to IC31a(+) (its
//   feedthrough-null current is ~-60 dB below signal at audio rates; the
//   SPICE deck keeps it), 4558 GBW and rail clipping (ideal opamps on both
//   sides of the referee).
//
// Interface: mono in -> mono out (pin 26 AM SIG OUT).
//   input_sel routes the input to the upper (0) or lower (1) mixer channel;
//   ttones=1 replaces the inputs with internal test oscillators (carrier ->
//   upper channel, modulator -> AM MOD IN) so the offline impulse driver can
//   run the two-tone AM validation without a second input channel.
import("stdfaust.lib");

// --- controls ---------------------------------------------------------------
bal = hslider("bal", 0.5, 0.0, 1.0, 0.001);        // KBD VOLUME BALANCE (1=upper)
lvl = hslider("lvl", 0.05, 0.0, 1.0, 0.001);       // VR303 LEVEL trimmer
bias = hslider("bias", 0.40, 0.0, 1.0, 0.0001);    // VR302 BIAS trimmer (0.4110 = ring null)
rbal = hslider("rbal", 0.5, 0.0, 1.0, 0.001);      // VR301 RING BAL trimmer
intensity = hslider("intensity", 1.0, 0.0, 1.0, 0.001);  // panel AM INTENSITY
inputSel = nentry("input_sel", 0, 0, 1, 1);        // 0: upper channel, 1: lower
corrOn = nentry("corr", 1, 0, 1, 1);               // JFET channel-voltage correction
ttones = nentry("ttones", 0, 0, 1, 1);             // internal two-tone test source
fcar = nentry("fcar", 2000, 1, 20000, 0.1);        // carrier freq (upper channel)
acar = nentry("acar", 0.02, 0, 10, 0.0001);        // carrier amplitude (peak)
fmodv = nentry("fmod_hz", 200, 0.1, 20000, 0.1);   // modulator freq (AM MOD IN)
// ("fmod" as a UI label crashes faust 2.70.3 - name collides with the
//  fmod primitive in that parser; fixed upstream by 2.85)
amod = nentry("amod", 0, 0, 10, 0.0001);           // modulator amplitude (peak)

// --- component values (netlists/klm62d-balance-am.cir) ----------------------
r201 = 1e3;    r202 = 22e3;   // upper mixer, gain -22
r207 = 1.2e3;  r208 = 22e3;   // lower mixer, gain -18.33
r203 = 220.0;  r209 = 220.0;  // output build-outs
r301 = 15e3;   r302 = 220.0;  // /69 attenuator
r303 = 100e3;  r304 = 100e3;  r305 = 22e3;  r306 = 120e3;  r307 = 220.0;
rbalpot = 100e3;   // ASSUMED: KBD VOLUME BALANCE pot value (not on the sheet)
rintpot = 50e3;    // AM INTENSITY: VALUE assumed 50k, rheostat wiring confirmed
rvr301 = 47e3;     // VR301 RING BAL, divider (dry top / wet bottom / wiper->sum)
rload = 1e6;       // ASSUMED: external load on pin 26
c201 = 10e-6;  c203 = 10e-6;  c301 = 10e-6;
// Q301 2SK30-GR, ASSUMED mid-spread: Idss = 4 mA, VTO = -1.5 V
vto = -1.5;
beta = 1.778e-3;   // Idss/VTO^2
gmin = 1e-9;       // cutoff conductance floor
vbtop = -14.9 * 10.0 / 43.0;  // top of VR302 in the R310/VR302 divider (-3.465 V)
rgate = 4.7e6 / 2.0;          // R308 || R309
cgate = 68e-12 + 6e-12 + 2e-12;  // C302 + CGS + CGD -> 890 Hz gate pole

// --- control-rate derived network conductances ------------------------------
gx1 = 1.0 / r301;
gx2 = 1.0 / r302;
gxx = gx1 + gx2;
r305eff = r305 + rvr301 * (1.0 - rbal);  // wet feed: R305 + VR301 bottom half
k34 = r306 * r304 / r305eff;
rcu = 1.0 / (2.0 * c201 * ma.SR);  // trapezoidal companion resistances
rcl = 1.0 / (2.0 * c203 * ma.SR);
rc3 = 1.0 / (2.0 * c301 * ma.SR);
gup = 1.0 / (r203 + max(rbalpot * (1.0 - bal), 1.0) + rcu);  // upper leg
glp = 1.0 / (r209 + max(rbalpot * bal, 1.0) + rcl);          // lower leg
gzr = 1.0 / (r303 + rvr301 * rbal);       // dry feed: R303 + VR301 top half
girt = 1.0 / max(rintpot * intensity, 1.0);                  // intensity rheostat
qd = 1.0 / r307 + girt + 1.0 / rload;                        // out26 node admittance

// --- per-sample network solve -----------------------------------------------
// States (fed back with one-sample delay): companion histories hu/hl/h3 and
// the previous channel voltages vxp/vnsp seeding the JFET correction.
step(hu, hl, h3, vxp, vnsp, u, l, mf) = huN, hlN, h3N, vx, vns, o
with {
    vu = 0.0 - (r202 / r201) * u;
    vl = 0.0 - (r208 / r207) * l;
    vgf = vbtop * bias + mf;
    vov = max(vgf - vto, 0.0);
    gstat = max(2.0 * beta * vov, gmin);
    gcorr(vds) = max(beta * max(2.0 * vov - vds, 0.0), gmin);

    // linear solve for a given channel conductance g (compiler CSEs the
    // repeated calls): W-node/x-node/out26-node reduction of the companion
    // network, o = qw*w + qh*h3
    gj(g) = 1.0 / (1.0 / g + rc3);            // JFET branch: rds + C301 companion
    dxx(g) = 1.0 / (gxx + gj(g));
    qw(g) = (k34 * gj(g) * dxx(g) * gx1 - r306 * gzr) / (r307 * qd) + girt / qd;
    qh(g) = k34 * gj(g) * (dxx(g) * gj(g) - 1.0) / (r307 * qd);
    den(g) = gup + glp + gzr + gx1 * (1.0 - dxx(g) * gx1) + girt * (1.0 - qw(g));
    wv(g) = ((vu - hu) * gup + (vl - hl) * glp
             + (girt * qh(g) + gx1 * dxx(g) * gj(g)) * h3) / den(g);
    vxv(g) = dxx(g) * (wv(g) * gx1 + h3 * gj(g));
    i3v(g) = gj(g) * (vxv(g) - h3);
    vnsv(g) = h3 + i3v(g) * rc3;

    // two fixed-point refinements of g = beta*(2*vov - vds), seeded from the
    // previous sample's channel voltage
    g1 = ba.if(corrOn > 0.5, gcorr(vxp - vnsp), gstat);
    g2 = ba.if(corrOn > 0.5, gcorr(vxv(g1) - vnsv(g1)), gstat);

    w = wv(g2);
    vx = vxv(g2);
    i3 = i3v(g2);
    vns = vnsv(g2);
    o = qw(g2) * w + qh(g2) * h3;

    iu = (vu - hu - w) * gup;
    il = (vl - hl - w) * glp;
    huN = hu + 2.0 * rcu * iu;
    hlN = hl + 2.0 * rcl * il;
    h3N = h3 + 2.0 * rc3 * i3;
};

// --- sources ----------------------------------------------------------------
sine(f) = sin(2.0 * ma.PI * ph)
with {
    ph = (f / ma.SR) : (+ : ma.frac) ~ _;
};
usig(x) = ba.if(ttones > 0.5, acar * sine(fcar), x * (1.0 - inputSel));
lsig(x) = ba.if(ttones > 0.5, 0.0, x * inputSel);
// mod in -> LEVEL pot -> x0.5 gate divider -> 890 Hz gate-node pole
msig(x) = 0.5 * lvl * ba.if(ttones > 0.5, amod * sine(fmodv), 0.0)
    : si.smooth(ba.tau2pole(rgate * cgate));

process = _ <: (usig, lsig, msig) : (step ~ si.bus(5)) : (!, !, !, !, !, _);
