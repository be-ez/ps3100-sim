// PS-3100 composed voice: the validated board models wired as one Faust
// program. One note
// channel of the real signal path:
//
//   KLM-64E siggen -> KLM-69E KORG35 channel core (tracking HPF, junction
//   waveshaper, resonant 2-pole; the channel's own per-key gate envelope is
//   left out so the one envelope here stays the GEG - revisit with
//   key-assign/polyphony) -> KLM-76 VCA (VCA1 CONT driven by the KLM-76 GEG
//   trapezoid, the
//   GEG OUT normal; VCA2 CONT on the panel) -> KLM-62 resonators ->
//   KLM-76 ensemble.
//
// The GEG->VCA wire is the only cross-board connection made here; every
// other control stays the owning board's own UI element (unused elements
// from the libraries are pruned by the compiler). Inter-board level trims
// mirror web/voice/app.js until the KLM-71/77 output structure is modeled.
// No composite SPICE referee exists (no composite netlist); the per-board
// referees plus tests/test_instrument.py's chain sanity checks gate this
// file.
import("stdfaust.lib");
sigB = library("siggen.dsp");
gatB = library("gate.dsp");
gegB = library("geg.dsp");
vcaB = library("vca.dsp");
resB = library("resonator.dsp");
ensB = library("ensemble.dsp");

// GEG trapezoid (0..+5 V, own gate/delay/attack/release controls) scaled to
// the VCA's 0..1 CONT range and pushed through the vactrol dynamics exactly
// as the VCA's own CV path would
envCv = max(0.0, min(1.0, vgroup("geg", gegB.process) / 5.0));

// KORG35 channel core without its per-key envelope (see header): the
// gate board's 2x-oversampled tracking HPF -> junction nl -> resonant
// 2-pole, cutoff from its own vfc control
korg35(x) = vgroup("gate",
    x : gatB.up : gatB.hp1x2 : (gatB.nl, gatB.nl) : gatB.lp2x2
      : gatB.downHB : *(gatB.lingain));

// dual VCA with the envelope on VCA1, panel CONT on VCA2
gated(x) = vgroup("vca",
    x : vcaB.vcaStage(vcaB.rOf(envCv, 0.0))
      : *(vcaB.kload)
      : vcaB.vcaStage(vcaB.rOf(vcaB.cv2, 0.0)));

// trims between boards (see header): VCA out -> resonator in
process = vgroup("siggen", sigB.process) : *(0.05) : korg35 : *(20.0) : gated : *(0.5)
    : vgroup("resonator", resB.process) : vgroup("ensemble", ensB.process);
