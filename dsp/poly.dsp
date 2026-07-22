// PS-3100 polyphonic voice core: the whole keyboard, wired the way the real
// instrument is (no voice allocation - every key is a live channel all the
// time). Library file; the web/instrument owner wires it into the downstream
// GEG VCA -> resonator -> ensemble chain.
//
// Faithful topology reproduced here:
//   * KLM-64E SIGNAL GENERATORS: 12 master oscillators, one per pitch class,
//     each with its own per-note tuning cap (page-0010 chart). Each master
//     drives ONE binary octave-divider chain, so all 4 octave rows of a pitch
//     class are PHASE-LOCKED (they read the same counter); the 12 pitch
//     classes free-run independently, exactly like the 12 separate KLM-64
//     master VCOs. Shaper cell (staircase -> JFET follower -> PNP slicer/
//     folder) is reused verbatim from dsp/siggen.dsp (sigB.cell).
//   * KLM-69E GATE: 48 hardwired note channels (4 octave rows x 12 pitch
//     classes). Each channel = per-key attack/release envelope -> CD4007 VCA
//     -> KORG35 resonant low-pass. The KORG35 filter core (2x-oversampled
//     tracking-HPF / junction-nl / resonant 2-pole) is reused from
//     dsp/gate.dsp (gatB.up/hp1x2/nl/lp2x2/downHB/lingain). The FC cutoff bus
//     is SHARED (all notes see the same blended FCU/FCL bus - gate model
//     section 1), so the filter coefficients are computed once and fanned to
//     every voice; only the envelope/VCA and the filter STATE are per-voice.
//   * The 48 channels sum onto one output bus (the KLM-69 group "color
//     select" inverting summer). The summer ADDS the channels - it does not
//     average - so a dense chord is louder than one note, as on the hardware.
//
// Why re-derive the master law/divider/ladder instead of instantiating
// dsp/siggen.dsp 48x: a Faust library() reference shares ONE environment, so
// sigB.process referenced 48 times would share siggen's single note/octave UI
// (48 identical channels). The note index and octave row must be compile-time
// constants per voice, which cannot be injected into siggen's baked-in UI
// controls. So the frequency law, phase accumulator and octave-row ladder are
// re-expressed here (referencing siggen's *scalar constants* so they cannot
// drift), while the expensive, cleanly-parameterized pieces - the shaper cell
// and the KORG35 filter - are imported and reused. tests/test_poly.py
// (test_master_frequency_matches_siggen / test_octave_row_levels_match_siggen)
// pins the re-derivation against dsp/siggen.dsp.
//
// Label-collision gotcha (see dsp/instrument.dsp header): every shared control
// is a single distinct-labelled leaf, and the whole graph is wrapped in one
// vgroup("poly", ...). The 48 sigB.cell uses all reference the SAME sigB.wfd/
// wfr leaves (one shared control each), likewise gatB.vfc - so the fan-out
// merges rather than collides.
//
// Test hooks (offline renders via tests/impulse_driver.cpp, single output):
//   keys_lo / keys_hi  two 24-bit key bitmasks (exact in double). Bit
//                      (pc*4 + oct) high = that key held. pc 0..11 in the
//                      siggen note order (0=F,1=F#,..,7=C,..,11=E); oct 0..3 =
//                      octave rows (row k fundamental fm/2^(k+1)). Bits 0..23
//                      -> keys_lo, bits 24..47 -> keys_hi.
//   bypass_env         VCA = the raw gate (instant, no attack/release ramp)
//                      but still per-key: keyed voices open, unkeyed stay shut.
//   bypass_filter      output the summed post-VCA oscillator bus (raw
//                      staircase sum) instead of the KORG35 output, so the
//                      oscillator-structure tests can inspect the divider
//                      directly. NB it selects the output; it does NOT save
//                      CPU (Faust's select evaluates both branches).
//   sig_trim, bus_gain per-voice input trim / output bus gain (see level
//                      section). Defaults reproduce one dsp/instrument.dsp
//                      voice at the pre-VCA node.
import("stdfaust.lib");
sigB = library("siggen.dsp");
gatB = library("gate.dsp");

// ---- compile-time voice count (edit to trade CPU for polyphony) ------------
// Faithful default: 12 pitch classes x 4 octave rows = 48 KORG35 channels.
// Reduce NROW (drops the highest octave-row index per pitch class) or NPC to
// cut CPU; this is the documented voice-count parameter (never a silent
// reduction). The key bitmask layout stays pc*4+oct regardless.
NPC = 12;   // pitch classes (faithful 12; the 12 KLM-64 masters)
NROW = 4;   // octave rows per pitch class (faithful 4; the divider taps)

// ---- shared buses (one leaf each; wired to every consumer) -----------------
// temperament bus, volts at the KLM-62D pin (same law/units as siggen.dsp cv)
cv = hslider("cv", -1.62, -9.0, -0.55, 0.001);
// per-channel envelope RCs. `release` is the shared GATE RELEASE TERMINAL bus
//: relctl.dsp drives the physical terminal in
// volts; the volts->seconds panel conditioning is untranscribed, so the bus
// is exposed here directly in seconds (same convention as gate.dsp). See the
// wiring guide for how relctl slots in.
attack = hslider("attack", 0.1, 0.001, 1.0, 0.001);
release = hslider("release", 4.7, 0.05, 10.0, 0.01);
bypassEnv = checkbox("bypass_env");
bypassFilter = checkbox("bypass_filter");
// key state: two 24-bit masks, bit (pc*4+oct). Kept 24-bit so every bit is an
// exact integer in double regardless of host float handling.
keysLo = nentry("keys_lo", 0, 0, 16777215, 1);   // bits 0..23
keysHi = nentry("keys_hi", 0, 0, 16777215, 1);   // bits 24..47
// inter-board trims
sigTrim = nentry("sig_trim", 0.05, 0.0, 1.0, 0.0001);
busGain = nentry("bus_gain", 1.0, 0.0, 64.0, 0.001);

// ---- per-note tuning caps, pF: CT1+CT2 (page-0010 chart), mirrored from
// dsp/siggen.dsp. Selected by the compile-time pitch class. -----------------
ctList = (1547.0, 1470.0, 1380.0, 1300.0, 1220.0, 1150.0,
          1100.0, 1033.0, 970.0, 920.0, 867.0, 820.0);
ct(pc) = ba.take(pc + 1, ctList) * 1e-12;

// ---- master frequency law (KLM-64 charge-current source; identical to
// dsp/siggen.dsp, referencing its fitted scalar constants so the two files
// cannot drift) -------------------------------------------------------------
istep(i) = max((sigB.vbq - sigB.vt * log(max(i, sigB.imin) / 1e-14) - cv)
               / sigB.rr, sigB.imin);
ichg = max((sigB.vbq - 0.545 - cv) / sigB.rr, sigB.imin)
       : istep : istep : istep : istep : istep;   // 5 iterations, as siggen
fmPC(pc) = 1.0 / (sigB.dvq * ct(pc) / ichg + sigB.kdisq * ct(pc));

// ---- one binary counter per pitch class (the shared divider chain). Wraps
// at 2^4 = 16 so bits 0..3 cover the 4 octave rows; every row of this pitch
// class reads THIS counter -> phase-locked octaves. ------------------------
masterCount(pc) = (+(fmPC(pc) / ma.SR) : fmod(_, 16.0)) ~ _;   // ph in [0,16)

// ---- octave-row staircase from the shared counter bits (re-read ladder
// pools, identical to dsp/siggen.dsp; own tap = the highest bit) -----------
//
// The staircase is not evaluated as a signal here. Row k reads counter bits
// 0..k, so its ladder takes only 2^(k+1) distinct levels, and the shaper cell
// (sigB.cell) is a memoryless static solve - the re-read confirmed the cells
// have no capacitors - of that level against the wfd/wfr rails. So the cell is
// evaluated at the 2+4+8+16 = 30 possible levels with COMPILE-TIME bit
// patterns, which makes each one depend on nothing but the panel controls:
// Faust hoists all 30 solves into the control section (once per block), and
// the audio loop is left with a select over the precomputed levels. Every one
// of the 48 voices then costs a table read instead of ~14 exp + 9 log per
// sample. Identical output (float reassociation only, ~1e-15), ~11x less CPU
// - the 48-channel build only fits the browser's audio render quantum with
// this; see tests/test_poly.py::test_shaper_table_matches_direct_cell.
ccK(j, m) = fmod(floor(j / pow(2.0, m)), 2.0) - 0.5;   // bit(m) of const j, -0.5
nsRowK(0, j) = sigB.vmid + sigB.vsq * ccK(j, 0);
nsRowK(1, j) = sigB.vmid + sigB.vsq * (ccK(j, 1) + ccK(j, 0)) / 2.0;
nsRowK(2, j) = sigB.vmid + sigB.vsq * (2.0 * ccK(j, 2) + ccK(j, 1) + ccK(j, 0)) / 4.0;
nsRowK(3, j) = sigB.vmid
    + sigB.vsq * (3.9 * ccK(j, 3) + 1.95 * ccK(j, 2) + ccK(j, 1) + ccK(j, 0)) / 7.85;
// row k's level table and the counter's low k+1 bits as its index
nsteps(oct) = int(pow(2, oct + 1));
shapedRow(oct, idx) = par(j, nsteps(oct), sigB.cell(nsRowK(oct, j)))
                    : ba.selectn(nsteps(oct), idx);
idxOf(oct, ph) = fmod(floor(ph), pow(2.0, oct + 1));

// ---- per-key gate from the bitmask (pc,oct compile-time -> bit index folds) -
bitOf(mask, b) = fmod(floor(mask / pow(2.0, b)), 2.0);
gateOf(pc, oct) = ba.if(pc * 4 + oct < 24,
                        bitOf(keysLo, pc * 4 + oct),
                        bitOf(keysHi, pc * 4 + oct - 24));
// OR of every key = the raw keyboard trigger (KLM-69 pin 8) the trigger board
// / shared GEG consumes. Exported for the instrument owner; not in `process`.
anyKeyTrig = (keysLo + keysHi > 0.5);

// ---- per-voice envelope + CD4007 gate VCA (ported from dsp/gate.dsp; the
// panel ADSR conditioning is untranscribed so attack/release are seconds) ---
attPole = ba.tau2pole(attack);
relPole = ba.tau2pole(release);
envG(g) = g : onepole
with {
    onepole(x) = y ~ _
    with { y(fb) = x + (fb - x) * ba.if(x > 0.5, attPole, relPole); };
};
vcaGainG(g) = ba.if(bypassEnv > 0.5, g, gn)   // bypass_env: instant, still per-key
with {
    envc = envG(g) * 10.0;              // envelope cap, charges toward ~10 V
    envnV = 14.9 - 1.6 * envc;          // inverted CD4007 drive line
    vov = max(7.45 - envnV - 2.0, 0.0); // PMOS overdrive
    rl = 100e3;
    gn = ba.if(vov > 1e-6, rl / (rl + 1.0 / (0.4e-3 * max(vov, 1e-6))), 0.0);
};

// ---- shared-cutoff KORG35 channel (dsp/gate.dsp core; per-voice filter
// state, shared vfc coefficients) -------------------------------------------
korg35(x) = x : gatB.up : gatB.hp1x2 : (gatB.nl, gatB.nl) : gatB.lp2x2
              : gatB.downHB : *(gatB.lingain);

// ---- one note channel: master phase in, gated+filtered audio out ----------
voice(pc, oct) = osc : gvca : filt
with {
    osc(ph) = shapedRow(oct, idxOf(oct, ph)) * sigTrim;   // staircase, scaled
    gvca(x) = x * vcaGainG(gateOf(pc, oct));            // CD4007 gate VCA
    filt(x) = ba.if(bypassFilter > 0.5, x, korg35(x));
};

// each pitch class: ONE counter fanned to its octave rows (phase-lock); the
// 12 blocks free-run independently. All voices sum onto the group bus.
pcBlock(pc) = masterCount(pc) <: par(oct, NROW, voice(pc, oct));
process = vgroup("poly", par(pc, NPC, pcBlock(pc)) :> _ : *(busGain));
