// PS-3100 panel conditioning: the four CV utility boards that sit between the
// front-panel controls and the sound boards, composed into one control-rate
// program.
//
// These boards are what the panel's WAVE FORM selector, FINE/COARSE, CUTOFF
// FREQUENCY, KBD FILTER BALANCE and the RELEASE switch actually drive. Each
// is already transcribed and SPICE-refereed on its own; this file only wires
// them side by side so the browser panel can reach all four through a SINGLE
// AudioWorklet instead of four (they are DC/sub-audio boards - one worklet
// costs one render-quantum callback per block regardless of how little it
// computes, and the 48-channel voice core already owns most of the budget).
//
// No new circuit theory lives here. Every law comes from the library files,
// so the per-board referees (tests/test_{wavectl,freqctl,filterctl,relctl}_*)
// remain the referee for this file too; tests/test_panelctl.py only checks
// that the composition routes the right pin to the right output channel.
//
// Outputs, in channel order, all in VOLTS AT THE BOARD PINS:
//   0  WFR   KLM-63 wave form "ramp" rail   (pin 12)   ~0.0 .. +14.83
//   1  WFD   KLM-63 wave form "duty" rail   (pin 13)   ~0.0 .. +12.83
//   2  BUS   KLM-62D temperament bus        (pin 37)   ~-9.3 .. -0.5
//   3  FCU   KLM-63 filter upper bus        (pin 9)    -0.467 .. +0.035
//   4  FCL   KLM-63 filter lower bus        (pin 10)   -0.397 .. +0.108
//   5  REL   KLM-62D gate release terminal  (pin 5)    +0.14 .. +11.61
//
// Inputs (audio-rate modulation pins; the panel patch field feeds these):
//   0  PWM IN   -> wavectl pin 15
//   1  FC MOD 1 -> filterctl pin 6
//   2  FC MOD 2 -> filterctl pin 7
//
// The temperament bus has no audio-rate input here: freqctl's own mod pins
// (39/40/41/42/43/44) are exposed as controls by that file, and its servo
// drops out above ~430 Hz anyway (documented in dsp/freqctl.dsp).
import("stdfaust.lib");
wavB = library("wavectl.dsp");
frqB = library("freqctl.dsp");
fltB = library("filterctl.dsp");
relB = library("relctl.dsp");

// filterctl publishes (FCU, FCL, FC BIAS); FC BIAS is the open-circuit
// -14.9 V pin, of no use to a consumer, so it is dropped here.
fcBuses(m1, m2) = fltB.law(m1, m2) : (_, _, !);

process = vgroup("wave", wavB.rails),
          vgroup("freq", frqB.process),
          vgroup("filt", fcBuses),
          vgroup("rel", relB.process);
