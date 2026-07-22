// PS-3100 full-keyboard composed instrument.
//
// The 48-voice poly core (dsp/poly.dsp: 12 phase-locked divider chains x 4
// octave rows, per-key CD4007 gate envelopes + KORG35 cells, additive KLM-69
// group bus) replaces the mono siggen->KORG35 front of dsp/instrument.dsp.
// Everything downstream is instrument-global and identical to instrument.dsp:
// GEG-gated VCA (GEG OUT normal), resonators, ensemble.
//
// Keyboard trigger conditioning (KLM-76 KBD Trigger, dsp/trigger.dsp): the
// key COUNT rebuilds the keyboard bus with trigger.dsp's traced constants
// (Vstep/divider/edge smoothing referenced from the library so the two files
// cannot drift), then the SINGLE/MULTIPLE comparators condition it exactly as
// the board does:
//   SINGLE   holds while any key is down; legato does NOT retrigger.
//   MULTIPLE fires the ~40-80 ms AC-coupled pulse on every new key attack.
// The conditioned (active-low) level gates the shared GEG through the new
// signal entry gegB.envOf (active-low: asserted = pulled near 0 V, so the
// gate condition is level < 2 V).
import("stdfaust.lib");
polB = library("poly.dsp");
trgB = library("trigger.dsp");
gegB = library("geg.dsp");
vcaB = library("vca.dsp");
resB = library("resonator.dsp");
ensB = library("ensemble.dsp");

// --- KLM-76 trigger conditioning (constants from trgB; law per its netlist).
// The bus is driven by the key COUNT as its own control (referencing the poly
// core's keys_lo/keys_hi here would instantiate a second copy of those
// widgets under this group - Faust attaches widgets per group context - and
// the two copies would then have to be set in lockstep; the key owner sets
// nkeys alongside the bitmasks instead). ---
multiple = checkbox("multiple");   // panel TRIG mode: 0 = SINGLE, 1 = MULTIPLE
trig = vgroup("trigger", gateSig)
with {
    nHeld = nentry("nkeys", 0, 0, 48, 1);
    busDC(n) = (trgB.VCC / 300e3 + trgB.Vstep * n / (100.0 + trgB.Rkbd))
             / (1.0 / 300e3 + 1.0 / 1e3 + 1.0 / (100.0 + trgB.Rkbd));
    bus = busDC(nHeld) : si.smooth(ba.tau2pole(trgB.tauEdge));
    acn = bus : trgB.onehp(exp(-1.0 / ma.SR / trgB.tauMult));
    sngLevel = trgB.level(bus > trgB.VTH_SINGLE);   // active-low volts
    mulLevel = trgB.level(acn > trgB.VTH_MULT);
    gateSig = ba.if(multiple, mulLevel, sngLevel) < 2.0;
};

// --- shared GEG -> VCA (identical to instrument.dsp, gate from the trigger) ---
envCv = max(0.0, min(1.0, vgroup("geg", gegB.envOf(trig)) / 5.0));
gated(x) = vgroup("vca",
    x : vcaB.vcaStage(vcaB.rOf(envCv, 0.0))
      : *(vcaB.kload)
      : vcaB.vcaStage(vcaB.rOf(vcaB.cv2, 0.0)));

// --- full keyboard -> shared chain ---
process = vgroup("poly", polB.process) : *(20.0) : gated : *(0.5)
    : vgroup("resonator", resB.process) : vgroup("ensemble", ensB.process);
