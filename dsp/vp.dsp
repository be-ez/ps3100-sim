// KLM-76 Voltage Processors (PS3100), real-time model.
//
// Mirrors netlists/klm76-vp.cir: two identical CV utility channels, each a panel attenuverter
// built from a dual-gang pot (sections LMA/LMB) hung between the signal and
// a -5.00 V reference bus, an inverting 1M/1M input amp with a 159 Hz lag
// (C204/C205 1n across the 1M feedback), and an inverting output mixer
// whose R236/R242 3M pull from +14.9 V cancels the pots' -5 V pedestal:
//
//   OUT_n = (2g-1) * Vin_n + 0.03 V   (ideal; g = panel CONTROLLER knob)
//
// CW = non-inverting unity (through the lag), CCW = inverting unity
// (direct, un-lagged), center = null. The model is the EXACT linear network
// of the netlist, not the ideal law: each pot section is reduced to its
// bus admittance / injection, the shared -5.00 V bus (667R Thevenin from
// the -15 V 2K/1K divider) is solved in closed form across BOTH channels
// (so bus sag ~0.2 V at rpot=50k and the antiphase-cancelling crosstalk
// come out SPICE-exact), wipers are re-derived from the bus voltage, and
// the mixer applies the -(wa + wb + 14.9/3) sum with the house +/-13.4 V
// 4558 clip. rpot = 50k is an ASSUMPTION (panel part, value not on the
// sheet) - keep in sync with the netlist default.
//
// Paths: only the non-inverting (B) path passes the input amp, so it alone
// carries the 159 Hz one-pole lag (tau = R224*C204 = 1 ms); the inverting
// (A) path taps the input directly at the pot. At knob center the DC gain
// nulls but HF leaks at -6 dB through the lag mismatch (a first-order HPF
// toward the inverting phase) - validated against SPICE AC.
import("stdfaust.lib");

// --- controls ---
// knob1/knob2: panel CONTROLLER, 0..1. Gang mapping kb = g (section B
// wiper toward LM_n), ka = 1-g (section A wiper toward VPn IN); the
// opposite wiring is inferred from the offset-cancellation algebra.
knob1 = hslider("knob1", 1.0, 0.0, 1.0, 0.001);
knob2 = hslider("knob2", 1.0, 0.0, 1.0, 0.001);
// vin1/vin2: DC volts added to the audio-rate inputs (panel -5..+5 scale);
// test/manual-CV hook, also how the impulse_driver sets DC grid points
vin1 = hslider("vin1", 0.0, -20.0, 20.0, 0.001);
vin2 = hslider("vin2", 0.0, -20.0, 20.0, 0.001);
// ac=1: subtract the knob-dependent zero-input operating point and skip the
// clip - linear small-signal output for FFT-based referee tests
acmode = checkbox("ac");
// monitor: which channel drives output 0 of the offline driver (0/1)
monitor = nentry("monitor", 0, 0, 1, 1);

// --- netlist constants (designators, keep in sync with klm76-vp.cir) ---
rpot = 50.0e3;          // LM pot end-to-end (ASSUMED, not on the sheet)
r226 = 220.0;           // R226/R231, in series with section B's top
rmix = 1.0e6;           // R234/R235/R240/R241 wiper loads (virtual ground)
rth = 2.0e3 * 1.0e3 / 3.0e3;  // both reference dividers' Thevenin, 667R
vrefp = -15.0 / 3.0;    // pot cold-end bus, -5.000 V
voff = 14.9 / 3.0;      // R236/R242 3M from +14.9 V referred to the 1M fb
fLag = 1.0 / (2.0 * ma.PI * 1.0e6 * 1.0e-9);  // R224*C204, 159.15 Hz
vclip = 13.4;           // house 4558 swing on +/-14.9 V rails

clip(x) = max(-vclip, min(vclip, x));

// one-pole LP, bilinear (corner 159 Hz: warp negligible at audio rates)
lp1(fc, x) = (rec ~ _)
with {
    t = tan(ma.PI * fc / float(ma.SR));
    rec(y) = (t * (x + x') + (1.0 - t) * y) / (1.0 + t);
};

// full two-channel solve; x1/x2 in volts, g1/g2 knob fractions.
// Each pot section is a series top resistor rt (source side) to the wiper,
// rb from wiper to the shared bus, wiper loaded by rmix into the mixer's
// virtual ground: reduce every section to its bus admittance yb and
// injected current ib, solve the one-unknown bus node, then recover the
// wipers. Section A: top = input (direct), ka = 1-g so rt = rpot*g.
// Section B: top = clipped inverting amp through the 159 Hz lag, behind
// R226, kb = g so rt = rpot*(1-g) + R226.
vp2(g1, g2, x1, x2) = y1, y2
with {
    pr(k) = max(rpot * k, 1.0);  // 1R wiper floor, matches the netlist
    ra1(g) = pr(g);
    ra2(g) = pr(1.0 - g);
    rb1(g) = pr(1.0 - g) + r226;
    rb2(g) = pr(g);
    vb1 = clip(0.0 - lp1(fLag, x1));
    vb2 = clip(0.0 - lp1(fLag, x2));
    sg(rt, rb) = 1.0 / rt + 1.0 / rb + 1.0 / rmix;
    yb(rt, rb) = (1.0 / rb) * (1.0 / rt + 1.0 / rmix) / sg(rt, rb);
    ib(rt, rb, vs) = (1.0 / rb) * (1.0 / rt) / sg(rt, rb) * vs;
    ysum = yb(ra1(g1), ra2(g1)) + yb(rb1(g1), rb2(g1))
         + yb(ra1(g2), ra2(g2)) + yb(rb1(g2), rb2(g2));
    isum = ib(ra1(g1), ra2(g1), x1) + ib(rb1(g1), rb2(g1), vb1)
         + ib(ra1(g2), ra2(g2), x2) + ib(rb1(g2), rb2(g2), vb2);
    vbus = (vrefp / rth + isum) / (1.0 / rth + ysum);
    w(rt, rb, vs) = (vs / rt + vbus / rb) / sg(rt, rb);
    y1 = 0.0 - (w(ra1(g1), ra2(g1), x1) + w(rb1(g1), rb2(g1), vb1) + voff);
    y2 = 0.0 - (w(ra1(g2), ra2(g2), x2) + w(rb1(g2), rb2(g2), vb2) + voff);
};

// signal outputs: exact solve, minus the zero-input operating point in ac
// mode (offset depends on the knobs only; lp1 of a zero signal stays zero
// so the duplicate solve constant-folds). The compiler shares the repeated
// vp2 calls.
process = _, _ : proc
with {
    proc(a1, a2) = out1, out2
    with {
        yf1 = vp2(knob1, knob2, a1 + vin1, a2 + vin2) : _, !;
        yf2 = vp2(knob1, knob2, a1 + vin1, a2 + vin2) : !, _;
        yz1 = vp2(knob1, knob2, 0.0, 0.0) : _, !;
        yz2 = vp2(knob1, knob2, 0.0, 0.0) : !, _;
        y1 = ba.if(acmode, yf1 - yz1, clip(yf1));
        y2 = ba.if(acmode, yf2 - yz2, clip(yf2));
        out1 = select2(monitor, y1, y2);
        out2 = y2;
    };
};
