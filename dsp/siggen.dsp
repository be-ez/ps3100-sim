// KLM-64E signal generator, real-time model of one note channel.
//
// Mirrors netlists/klm64-siggen.cir:
//   temperament-bus pitch entry (KLM-62D bus -> VR11 PITCH -> R21 62k ->
//   Q11 emitter; Q11 is the charge CURRENT SOURCE, so the master period is
//   linear in the bus voltage and one bus octave - a doubling of
//   (rail - bus), rail = -0.55 V - doubles f) -> MM5824-style binary
//   divider (exact /2 stages, behavioral in SPICE too) -> octave-row
//   waveshaper: the row's resistor ladder builds a staircase from the
//   divider taps, a 2SK30 SOURCE FOLLOWER buffers it into a 2SA733 PNP
//   slicer/folder whose emitter hangs on BOTH rails (18k to WFR, D31 from
//   WFD). Real KLM-63 rail levels select the panel waveform:
//     saw      wfr 14.83, wfd ~0  : PNP saturated, output follows the
//                                    staircase;
//     triangle wfr 8.7-13.5, wfd ~0: staircase steps above the WFR-set
//                                    threshold fold down (inverting gain
//                                    ~ -100k/18k), TRI ADJ moves the fold;
//     pulse    wfr ~0.4, wfd 7.4-12.8: D31 pins the emitter at wfd-0.585 -
//                                    comparator; duty = fraction of the
//                                    staircase below it (1/8, 3/8, 7/8 for
//                                    the three panel widths; continuous
//                                    for PWM).
//   No audio input; one output, volts at the output pin (100k load).
//
// Frequency law (physics of the netlist charge path, constants fitted
// against ngspice transients over the 12-note x bus[-9.11,-0.8] grid,
// scratch fit rms 0.15%, max 0.37%):
//   I  solves I = (VB - Vbe(I) - vbus)/R   (Q11 current source; R = R21 +
//      VR11 at the trim calibration; VB = SPAN-wiper base bias; damped
//      fixed point, 6 unrolled iterations)
//   T = DV*Ct/I + KDIS*Ct       f = 1/T
// DV ~ the 6.65 V ramp swing; KDIS absorbs the behavioral reset's
// discharge tail and trip resolution. Calibration trim=0.99/span=1.0
// puts note A at 1760 Hz (A6 master assumption, see model doc) at the
// bus neutral -1.62 V - both trims near end-of-travel, flagged.
//
// Shaper cell: closed-form static solve mirrored from the netlist (the
// re-read confirmed the cells have NO capacitors): JFET-follower
// quadratic, a 6-step Newton solve of the saturated-PNP emitter node
// (collector-base feedback included - it is what flattens the pulse
// on-level), a damped fixed point for the active/cutoff branch, soft-min
// between branches. Validated against ngspice DC sweeps of the cell:
// staircase-level error <= 68 mV, comparator edges within 10 mV over the
// 8 panel rail cases.
//
// Comparison method (a free-running source does not fit the impulse
// harness): tests/test_siggen_dsp.py compares (a) oscillation frequency
// via the once-per-staircase-cycle large downward step, and (b)
// staircase-cycle Fourier coefficients against the same integrals over
// the SPICE transient.
//
// Not modeled: band-limiting of the staircase edges
// (naive ZOH synthesis; polyBLEP deferred), footage-gate switching (rows
// are the 8' tap set), WFD/WFR rail dynamics (owned by dsp/wavectl.dsp).
import("stdfaust.lib");

// --- controls ---
// note: 0=F 1=F# ... 11=E (card-1 then card-2 stuffing order, page 0010)
note = nentry("note", 4, 0, 11, 1);
// temperament-bus voltage at the card pin (KLM-62D pin 37 law: -1.62 V
// neutral, -9 V practical bottom, more negative = higher pitch).
// SEMANTIC CHANGE from the pre-re-read model (was an FM offset around 0).
cv = hslider("cv", -1.62, -9.0, -0.55, 0.001);
// output row 0..3 = card rows 1..4 at the 8-foot tap set: row k's staircase
// fundamental is fm/2^(k+1); its ladder takes the own tap via 100k plus the
// higher-octave taps per the re-read pools (row 2: one-up 100k; row 3:
// one/two-up 200k/200k; row 4: 200k/390k/390k)
octave = nentry("octave", 2, 0, 3, 1);
// waveform rails from KLM-63 (volts at pins 6/7; see the mode table above).
// RANGES WIDENED to the real rail spans (wfd 0-12.83 V, wfr 0-14.83 V);
// defaults = panel sawtooth
wfd = hslider("wfd", 0.0, 0.0, 13.0, 0.001);
wfr = hslider("wfr", 14.83, 0.0, 14.9, 0.001);
// staircase source swing (pk-pk) and center at the shaper gates (divider
// tap swing after the squaring buffers; not readable from the scan -
// chosen so the measured WFD ladder slices the staircase over its full
// span; flagged for hardware measurement)
vsq = nentry("vsq", 5.2, 0.0, 10.0, 0.01);
vmid = nentry("vmid", 7.45, 0.0, 14.9, 0.01);

// --- per-note tuning capacitance: CT1 + CT2 in parallel, pF (page 0010) ---
ctPF = (1547.0, 1470.0, 1380.0, 1300.0, 1220.0, 1150.0,
        1100.0, 1033.0, 970.0, 920.0, 867.0, 820.0) : ba.selectn(12, note);
ct = ctPF * 1e-12;

// --- master frequency law (see header) ---
vt = 0.02585;      // kT/q at the house 27 C model temperature
vbq = -0.039159;   // fitted SPAN-wiper bias minus residual offsets (span=1.0)
rr = 71.9e3;       // R21 62k + VR11 10k * trim 0.99 (A -> 1760 calibration)
dvq = 6.62863;     // fitted ramp swing (netlist: 6.95 release -> 0.30 trip)
kdisq = 4954.90;   // discharge time per farad (R141 tail + trip resolution)
imin = 2e-8;       // law guard: bus above ~-0.6 V starves Q11 (f -> ~0)
// undamped fixed point: contraction VT/(R*I) stays below ~0.55 over the
// whole control range, so 5 iterations land within 1e-6 of the fit
istep(i) = max((vbq - vt * log(max(i, imin) / 1e-14) - cv) / rr, imin);
ichg = max((vbq - 0.545 - cv) / rr, imin)
       : istep : istep : istep : istep : istep;
fm = 1.0 / (dvq * ct / ichg + kdisq * ct);

// --- divider (exact /2 chain = binary cycle counter, like the MM5824
// ripple stages): bit m of the master-cycle count = tap fm/2^(m+1) ---
wrapN = pow(2.0, octave + 1.0);
ph = (+(fm / ma.SR) : fmod(_, wrapN)) ~ _;
cnt = floor(ph);
bit(m) = fmod(floor(cnt / pow(2.0, m)), 2.0);

// --- row ladder: conductance-exact sums of the re-read resistor pools,
// own tap = the highest bit (slowest). All taps swing vmid +- vsq/2. ---
c(m) = bit(m) - 0.5;
nsRow0 = vmid + vsq * c(0);
nsRow1 = vmid + vsq * (c(1) + c(0)) / 2.0;
nsRow2 = vmid + vsq * (2.0 * c(2) + c(1) + c(0)) / 4.0;
nsRow3 = vmid + vsq * (3.9 * c(3) + 1.95 * c(2) + c(1) + c(0)) / 7.85;
ns = (nsRow0, nsRow1, nsRow2, nsRow3) : ba.selectn(4, octave);

// --- shaper cell (see header; constants fitted on the ngspice DC sweep) ---
betaq = 1.78e-3;   // 2SK30A-GR
af = betaq * 22e3;
nvt = 1.7 * vt;    // D31 emission
vd = 0.585;        // D31 drop at the pulse-mode pin current
vsat = 0.02;       // PNP quasi-saturation offset (SPICE op)
vebsat = 0.66;     // emitter-base drop in the saturated branch
voff = 0.041;      // comparator-edge base lift (partial C-B feedback)
wsm = 0.18;        // soft-min width at the fold corner
cell(nsv) = out
with {
    u = nsv + 1.5;
    xf = (-1.0 + sqrt(1.0 + 4.0 * af * (u + 14.9))) / (2.0 * af);
    s = u - xf;                             // pure source-follower output
    idio(e) = 2.5e-9 * exp(min((wfd - e) / nvt, 26.0));
    i18(e) = (wfr - e) / 18e3;
    // saturated branch: emitter-node KCL with C-B feedback, Newton x6
    xj(e) = max(u - (e - vebsat), 0.0);
    fsat(e) = i18(e) + idio(e) - max(e - vsat, 0.0) / 122e3
              - ((e - vebsat) + 14.9) / 22e3 + betaq * xj(e) * xj(e);
    dfsat(e) = 0.0 - 1.0 / 18e3 - idio(e) / nvt - 1.0 / 122e3
               - 1.0 / 22e3 - 2.0 * betaq * xj(e);
    nstep(e) = e - fsat(e) / dfsat(e);
    esat = max(s + vebsat, wfd - vd)
           : nstep : nstep : nstep : nstep : nstep : nstep;
    nsat = 0.8197 * (esat - vsat);
    // active/cutoff branch: base held at the follower, damped veb solve
    astep(e) = 0.5 * (e + s + vt * log(max(i18(e), 1e-9) / 1e-14));
    ea = max(s + 0.62 : astep : astep : astep : astep
             : astep : astep : astep : astep, wfd - vd);
    isrc = 0.9967 * (max(i18(ea), 0.0) + idio(ea));
    iexp = 1e-14 * exp(min((ea - s + voff) / vt, 26.0));
    nact = 100e3 * min(iexp, isrc);
    lo = min(nsat, nact);
    hi = max(nsat, nact);
    out = max(lo - wsm * log(1.0 + exp(0.0 - (hi - lo) / wsm)), 0.0);
};

process = cell(ns);
