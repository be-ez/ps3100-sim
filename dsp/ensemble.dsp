// KLM-76 ENSEMBLE (PS-3100), real-time model.
//
// Mirrors netlists/klm76-ensemble.cir
//
//   in -> R401/R402 divider (-6 dB) + C401 pole (~13.8 kHz)
//      -> IC-44a pre-emphasis shelf (DC gain 1, zero 6.25 kHz, poles ~72 kHz)
//      -> IC-44b R406/C404 anti-alias pole (~15.4 kHz)
//      -> two BBD channels: C410 1.6 Hz HPF -> MN3004 512-stage BBD
//         (ideal fractional delay, tau = 256/fclk) -> 3-pole reconstruction
//         ladder (real poles 3841.7 / 28570 / 148195 Hz) -> 2SA733 follower
//         (SPICE-fitted flat gain 0.96839) -> 0.033u/100k wet leg (48 Hz HPF)
//      -> IC-32 inverting mixer: out = -(wet1 + wet2 + (100k/180k)*dry).
//
//   Each channel's clock astable is a VCO: fclk = F0V + KV*Vfm (linear fit of
//   the SPICE transient law, residual < 50 Hz over the full ring swing),
//   modulated by its own CD4069 three-stage integrator-ring LFO (three
//   phases 120 deg apart; U6, ONE ring
//   output is wired DC-DIRECT to the two 130k base resistors, so
//   Vfm = VMID + A_LFO*sin - no divider, no coupling cap).
//   tau_i = 256/fclk_i: ~5.0 ms center, 3.97..6.80 ms excursion (~+/-28%).
//
// Behavioral simplifications:
// no BBD zero-order-hold sinc / clock feedthrough / compander (none fitted),
// LFO treated as a sine at the SPICE-measured rate (the ring output is a
// soft-clipped near-sine), single-phase FM pick-off (as wired).
//
// All SPICE-fitted constants below are re-derived from the netlist by
// tests/test_ensemble_spice.py; tests/test_ensemble_dsp.py holds this file's
// response against ngspice at frozen-LFO snapshots.
import("stdfaust.lib");

// --- component values (netlists/klm76-ensemble.cir refdes) ---
r401 = 47e3;  r402 = 47e3;  r403 = 1e3;   c401 = 470e-12;
r404 = 2.2e3; r405 = 22e3;  c402 = 100e-12; c403 = 1000e-12;
r406 = 22e3;  c404 = 470e-12;
c410 = 1e-6;  r411 = 220.0; r412 = 100e3;
r464 = 100e3; r466 = 100e3; r465 = 180e3; c430 = 0.033e-6;

// reconstruction ladder real poles (Hz): roots of
// 1 + a1 s + a2 s^2 + a3 s^3 with R1=R422||R423=1.1k C1=C414 1n, R2=R424
// 47k C2=C432 680p, R3=R425 47k C3=C415 150p (exact ladder polynomial;
// factored offline, numpy.roots - all real):
ladP1 = 3841.686;
ladP2 = 28570.334;
ladP3 = 148195.370;
// 2SA733 emitter-follower insertion gain: SPICE-fitted flat -0.279 dB
// (base loading of the 95k ladder source; residual shape < 0.08 dB)
kfol = 0.96839;
// dry mixer leg gain R466/R465
kdry = r466 / r465;

// --- SPICE-fitted modulation constants ---
// clock astable law (transient sweep of clkvco subckt over the ring output
// range 0.25..14.65 V, the FM node's actual swing per the U6 re-read):
f0v = 37209.9;   // Hz, linear-fit intercept at Vfm = 0
kv  = 1868.74;   // Hz/V (linear fit residual < 50 Hz)
nbbd = 256.0;    // MN3004: 512 stages -> tau = 512/(2 fclk)
// CD4069 ring LFO rates (SPICE transient, behavioral inverter gain 30):
// linear phase-shift theory sqrt(3)/(2 pi 3.3M C) gives 2.14 / 1.78 Hz;
// rail clipping raises both by the same 1.43x factor
lfoFA = 3.0599;  // C = 0.039u (LFO A, channel 1)
lfoFB = 2.5391;  // C = 0.047u (LFO B, channel 2)
// FM node level: one ring output wired DC-direct to the 130k pair (U6
// re-read; CD4069 on 0/+14.9 V): midpoint +7.45 V, +/-7.20 V rail-clipped
vmid = 7.45;
afm = 7.20;

// --- controls / test hooks ---
bypass = checkbox("bypass");            // panel ensemble off: pass dry input
freeze = checkbox("lfo_freeze");        // test hook: freeze both LFOs
phaseA = hslider("lfo_phase_a", 0.0, 0.0, 1.0, 0.001);  // frozen phase, turns
phaseB = hslider("lfo_phase_b", 0.0, 0.0, 1.0, 0.001);
// monitor (test hook): 0 audio, 1 Vfm A [V], 2 tau1 [ms], 3 Vfm B, 4 tau2
monitor = nentry("monitor", 0, 0, 4, 1);
// per-channel wet gains (test hooks, mirror the netlist G1/G2 params;
// hardware has no such control)
g1 = nentry("g1", 1.0, 0.0, 1.0, 0.001);
g2 = nentry("g2", 1.0, 0.0, 1.0, 0.001);

// --- s-domain helpers: raw-coefficient wrappers over fi.tf1s/fi.tf2s ---
// H(s) = (n1 s + n0)/(d1 s + d0), bilinear prewarped at w1
tf1sraw(n1, n0, d1, d0, w1) = fi.tf1s(n1 / d1, n0 / (d1 * w1), d0 / (d1 * w1), w1);
// H(s) = (n2 s^2 + n1 s + n0)/(d2 s^2 + d1 s + d0)
tf2sraw(n2, n1, n0, d2, d1, d0, w1) =
    fi.tf2s(n2 / d2, n1 / (d2 * w1), n0 / (d2 * w1 * w1), d1 / (d2 * w1), d0 / (d2 * w1 * w1), w1);
// prewarp point kept below the bilinear singularity: poles above 0.2*SR are
// mapped with the warp zeroed at 0.2*SR instead (their in-band effect is a
// gentle droop; error quantified in tests/test_ensemble_dsp.py tolerances)
wsafe(w) = min(w, 2.0 * ma.PI * 0.2 * ma.SR);

// --- input chain (shared by both channels) ---
rdivth = r401 * r402 / (r401 + r402) + r403;  // Thevenin src of C401
inDiv = tf1sraw(0.0, 0.5, c401 * rdivth, 1.0, wsafe(1.0 / (c401 * rdivth)));
// IC-44a shelf: 1 + (R405||C402)/(R404 + 1/sC403)
shN2 = r404 * r405 * c403 * c402;
shN1 = r404 * c403 + r405 * c402 + r405 * c403;
shD2 = r404 * c403 * r405 * c402;
shD1 = r404 * c403 + r405 * c402;
preEmph = tf2sraw(shN2, shN1, 1.0, shD2, shD1, 1.0, wsafe(2.0 * ma.PI * 6250.0));
stage2 = tf1sraw(0.0, 1.0, r406 * c404, 1.0, wsafe(1.0 / (r406 * c404)));
front = inDiv : preEmph : stage2;

// --- per-channel wet elements ---
bbdInHP = tf1sraw(c410 * r412, 0.0, c410 * (r411 + r412), 1.0, 1.0 / (c410 * (r411 + r412)));
polelp(fp) = tf1sraw(0.0, wp, 1.0, wp, wsafe(wp)) with { wp = 2.0 * ma.PI * fp; };
ladder = polelp(ladP1) : polelp(ladP2) : polelp(ladP3);
wetLegHP = tf1sraw(r466 * c430, 0.0, r464 * c430, 1.0, 1.0 / (r464 * c430));

// --- three-phase LFO + clock law ---
phasor(f) = (+(f / ma.SR) ~ ma.frac);
// the three ring phases (120 deg apart, validated against SPICE); the FM
// pick-off is the first-stage output, wired DC-direct (U6 re-read)
lfo3(ph) = sin(2.0 * ma.PI * ph), sin(2.0 * ma.PI * (ph + 1.0 / 3.0)), sin(2.0 * ma.PI * (ph + 2.0 / 3.0));
vfmOf(f, frozenPhase) = vmid + afm * (select2(freeze, phasor(f), frozenPhase) : lfo3 : _, !, !);
vfmA = vfmOf(lfoFA, phaseA);
vfmB = vfmOf(lfoFB, phaseB);
tauOf(vfm) = nbbd / (f0v + kv * vfm);
tau1 = tauOf(vfmA);
tau2 = tauOf(vfmB);

// MN3004 as ideal fractional delay (5th-order Lagrange; tau*SR ~ 190-330
// samples at 48k, max 4096 covers the 6.8 ms delay ceiling up to 192k)
bbd(tau) = de.fdelay5(4096, tau * ma.SR);

wet(tau) = bbdInHP : bbd(tau) : ladder : *(kfol) : wetLegHP;

// --- full board ---
ensemble(x) = 0.0 - (w1 + w2 + kdry * x)
with {
    pre = x : front;
    w1 = g1 * (pre : wet(tau1));
    w2 = g2 * (pre : wet(tau2));
};

audio(x) = select2(bypass, ensemble(x), x);

process(x) = (audio(x), vfmA, tau1 * 1000.0, vfmB, tau2 * 1000.0) : ba.selectn(5, monitor);
