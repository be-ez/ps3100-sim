// PS-3100 full-panel layout, traced from a front-panel photograph.
//
// Coordinates are "panel units" on a 1465 x 462 field - the pixel geometry of
// the traced photo with the panel's top-left corner as the origin - so every
// position here can be checked back against the source image. app.js scales
// the whole field to the viewport; nothing in this file knows about CSS px.
//
// Each control carries a `status` saying how honest it is:
//   live  - bound to a parameter of a SPICE-refereed board model right now
//   soon  - the board IS modeled and refereed in dsp/, but the wasm/DSP work
//           to reach it from here is not done (see the phase notes in each
//           entry). Rendered inert with a distinct treatment.
//   panel - a real panel function implemented outside any modeled circuit
//           (e.g. FINAL VOLUME as a Web Audio gain: KLM-77 is not modeled)
//   inert - no circuit model exists anywhere in the repo. Never faked.
// `bind` names a channel in app.js's binding table; layout stays presentation.

export const PANEL = { w: 1465, h: 462 };

// ---- silkscreen section boxes -------------------------------------------
// x,y,w,h in panel units; `title` is the boxed legend across the top.
export const SECTIONS = [
  { id: "temperament", title: "TEMPERAMENT ADJUST", x: 0, y: 0, w: 90, h: 462, titleAt: "top" },
  { id: "siggen", title: "SIGNAL GENERATORS", x: 90, y: 0, w: 148, h: 462 },
  { id: "dlpf", title: "DYNAMIC\nLP FILTERS", x: 238, y: 0, w: 89, h: 462 },
  { id: "envmod", title: "ENVELOPE\nMODIFIERS", x: 327, y: 0, w: 95, h: 326 },
  { id: "resonators", title: "RESONATORS", x: 422, y: 0, w: 146, h: 326 },
  { id: "sh", title: "SAMPLE & HOLD", x: 327, y: 326, w: 241, h: 136, titleAt: "inner" },
  { id: "tsm", title: "TOTAL SIGNAL MODIFIERS", x: 568, y: 0, w: 610, h: 150, titleAt: "left" },
  { id: "monitor", title: "", x: 1178, y: 0, w: 157, h: 150 },
  { id: "power", title: "POWER", x: 1335, y: 0, w: 130, h: 150, titleAt: "right" },
  { id: "mg1", title: "", x: 568, y: 150, w: 92, h: 312 },
  { id: "geg", title: "GENERAL ENVELOPE GENERATOR", x: 660, y: 150, w: 155, h: 312, titleAt: "inner" },
  { id: "mg2", title: "", x: 660, y: 255, w: 88, h: 207, inset: true },
  { id: "vp", title: "VOLTAGE\nPROCESSORS", x: 815, y: 150, w: 120, h: 312, titleAt: "inner" },
  { id: "patch", title: "", x: 935, y: 150, w: 530, h: 312 },
];

// ---- knob scale legends --------------------------------------------------
// The real skirts are numbered; `scale` picks which numeral set is drawn
// around the 270-degree sweep.
export const SCALES = {
  ten: ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
  bipolar: ["-5", "-4", "-3", "-2", "-1", "0", "+1", "+2", "+3", "+4", "+5"],
};

const K = (id, x, y, d, label, o = {}) =>
  ({ kind: "knob", id, x, y, d, label, scale: "ten", status: "inert", ...o });
const T = (id, x, y, label, o = {}) =>
  ({ kind: "trim", id, x, y, d: 24, label, status: "inert", ...o });
const SEL = (id, x, y, d, label, positions, o = {}) =>
  ({ kind: "selector", id, x, y, d, label, positions, status: "inert", ...o });
const SW = (id, x, y, label, positions, o = {}) =>
  ({ kind: "slide", id, x, y, label, positions, status: "inert", ...o });
// bus taps are the orange sliders sitting on the modulation flow lines;
// two-position, and `label` is the ON legend printed beside them
const TAP = (id, x, y, label, o = {}) =>
  ({ kind: "bustap", id, x, y, label: "", positions: ["", label], status: "inert", ...o });

// Temperament: 12 per-note trims, note letters silkscreened to the left.
// poly.dsp bakes the per-note tuning caps as a compile-time ctList, so these
// have nowhere to land until that becomes a trim bus (phase 4).
const TEMPERAMENT = ["B", "A#", "A", "G#", "G", "F#", "F", "E", "D#", "D", "C#", "C"]
  .map((note, i) => T(`temp-${note}`, 44, 48 + i * 35.5, note, {
    status: "soon", bind: `temperament:${note}`,
    note: "poly.dsp ctList is compile-time; needs a per-note trim bus",
  }));

export const CONTROLS = [
  ...TEMPERAMENT,

  // ---- SIGNAL GENERATORS (KLM-64E siggen + KLM-63 wavectl + KLM-62D freqctl)
  SEL("waveform", 158, 80, 54, "WAVE FORM",
    ["TRI", "SAW", "RECT", "┐─REC", "┐─PUL", "PWM"],
    { status: "live", bind: "waveform" }),
  K("pwm-int", 165, 160, 44, "PULSE WIDTH\nMODULATION INTENSITY",
    { status: "live", bind: "pwmInt" }),
  K("fine", 128, 224, 36, "FINE", { scale: "bipolar", status: "live", bind: "fine" }),
  K("coarse", 198, 224, 36, "COARSE", { scale: "bipolar", status: "live", bind: "coarse" }),
  K("scale", 160, 287, 54, "SCALE", { status: "panel", bind: "scale",
    note: "octave/footage select - keyboard wiring, no board model" }),
  TAP("fm-tap", 112, 326, "", { status: "panel", bind: "fmTap" }),
  SW("reverse", 113, 348, "REVERSE", ["", "ON"], { status: "panel", bind: "reverse",
    note: "freqctl.dsp modr1/modr2 (the MOD-R pin pair IS reverse)" }),
  K("mg1-int", 170, 378, 44, "MG 1\nINTENSITY\nCONTROL",
    { labelSide: "left", status: "panel", bind: "mg1ToFreq" }),
  K("geg-int", 174, 430, 44, "GEG OR EXT\nINTENSITY\nCONTROL",
    { labelSide: "left", status: "panel", bind: "gegToFreq" }),

  // ---- DYNAMIC LP FILTERS (KLM-69E gate + KLM-63 filterctl)
  K("cutoff", 285, 80, 54, "CUTOFF FREQUENCY", { status: "live", bind: "cutoff" }),
  K("peak", 280, 160, 44, "PEAK", { status: "inert",
    note: "KORG35 Q is a fitted function of cutoff only; no resonance control modeled" }),
  // filterctl's vbal is reachable, but poly.dsp sums all 48 channels onto one
  // shared FC bus, so moving BAL would shift every note's cutoff instead of
  // balancing the halves. Left unwired until the voice split (phase 4).
  K("kbd-filter-bal", 282, 224, 36, "KBD FILTER BALANCE", { scale: "bipolar", status: "soon",
    bind: "kbdFilterBal",
    note: "filterctl vbal is live, but needs poly.dsp's upper/lower voice split to mean anything" }),
  K("expand", 283, 290, 44, "EXPAND", { status: "soon", bind: "expand",
    note: "gate.dsp expand is fitted; poly.dsp does not expose it (phase 4)" }),
  TAP("cutoff-mod-tap", 228, 326, "", { status: "panel", bind: "cutoffModTap" }),
  TAP("cutoff-on-tap", 318, 326, "ON", { status: "panel", bind: "cutoffOnTap" }),
  K("mg1-filt", 292, 378, 44, "MG 1", { labelSide: "left", status: "panel", bind: "mg1ToCutoff" }),
  K("geg-filt", 294, 430, 44, "GEG\nOR\nEXT", { labelSide: "left", status: "panel", bind: "gegToCutoff" }),

  // ---- ENVELOPE MODIFIERS (KLM-69E per-key envelope + KLM-62D relctl)
  K("attack-time", 373, 80, 54, "ATTACK TIME", { status: "live", bind: "attack" }),
  K("decay-time", 373, 160, 44, "DECAY TIME", { status: "inert",
    note: "gate.dsp: panel ADSR conditioning block is not transcribed" }),
  K("sustain", 373, 224, 44, "SUSTAIN LEVEL", { status: "inert",
    note: "gate.dsp: panel ADSR conditioning block is not transcribed" }),
  SW("release-mode", 355, 294, "RELEASE", ["RELEASE", "HALF D", "DAMPED"],
    { status: "live", bind: "releaseMode" }),
  SW("kbd-hold", 402, 294, "KBD HOLD", ["", "ON"], { status: "inert",
    note: "no board model" }),

  // ---- RESONATORS (KLM-62)
  K("res-intensity", 488, 80, 54, "RESONANCE INTENSITY", { status: "live", bind: "blend" }),
  K("res1", 457, 160, 44, "RESONATOR 1", { status: "live", bind: "peak1" }),
  K("res2", 457, 224, 44, "RESONATOR 2", { status: "live", bind: "peak2" }),
  K("res3", 457, 290, 44, "RESONATOR 3", { status: "live", bind: "peak3" }),
  SW("peak-mod-mg2", 529, 202, "PEAK FREQUENCY\nMODULATION\nBY MG 2", ["", "ON"],
    { status: "live", bind: "mg2ToRes" }),
  K("ext-peak-int", 529, 297, 44, "EXTERNAL\nPEAK FREQUENCY\nMODULATION\nINTENSITY CONTROL",
    { status: "live", bind: "extToRes" }),

  // ---- SAMPLE & HOLD (KLM-76)
  K("sh-clock", 462, 402, 54, "CLOCK FREQUENCY", { status: "live", bind: "shClock" }),
  SW("synchro", 535, 402, "SYNCHRO", ["", "ON"], { status: "inert",
    note: "external clock sync not modeled" }),

  // ---- TOTAL SIGNAL MODIFIERS (KLM-62D balance/AM + KLM-76 VCA + ensemble)
  K("am-int", 612, 82, 54, "AMPLITUDE MODULATOR", { status: "soon", bind: "amInt",
    note: "balance_am.dsp intensity - wasm exists, not yet in the chain (phase 2)" }),
  SW("ensemble", 738, 114, "", ["", "ON"], { status: "live", bind: "ensemble" }),
  TAP("am-tap", 633, 150, "ON", { status: "soon", bind: "amTap" }),
  TAP("vca1-tap", 826, 150, "ON", { status: "live", bind: "vca1Tap" }),
  TAP("vca2-tap", 920, 150, "ON", { status: "live", bind: "vca2Tap" }),
  K("kbd-vol-bal", 1013, 82, 54, "KEYBOARD VOLUME BALANCE", { scale: "bipolar", status: "soon",
    bind: "kbdVolBal", note: "balance_am.dsp bal; poly.dsp sums one bus, no upper/lower (phase 4)" }),
  K("final-volume", 1120, 82, 54, "FINAL VOLUME", { status: "panel", bind: "finalVolume",
    note: "KLM-77 output board not modeled - Web Audio gain" }),

  // ---- PHONE / DIRECT (KLM-77, not modeled)
  K("phone-volume", 1222, 82, 44, "PHONE VOLUME", { status: "panel", bind: "phoneVolume",
    note: "KLM-77 not modeled - Web Audio gain" }),
  K("direct-volume", 1297, 82, 44, "DIRECT VOLUME", { status: "panel", bind: "directVolume",
    note: "KLM-77 not modeled - Web Audio gain" }),

  // ---- POWER
  { kind: "lamp", id: "power-lamp", x: 1393, y: 56, d: 18, status: "live" },
  { kind: "power", id: "power", x: 1393, y: 104, d: 34, status: "live", bind: "power" },

  // ---- MODULATION GENERATOR 1 (KLM-63 MG1 + noise)
  // Selector position -> board output pin is NOT resolved in the repo: the
  // netlist header says the panel wiring is cross-board, same as the FREQ
  // CONT pots. Square/pink/white are unambiguous; the three shape positions
  // are provisional - see the note in app.js's MG1_OUTSEL.
  SEL("mg1-wave", 600, 197, 44, "",
    ["TRI", "SAW↓", "SAW↑", "┐─REC", "PINK", "WHITE"],
    { status: "live", bind: "mg1Wave", provisional: true }),
  { kind: "lamp", id: "mg1-lamp", x: 613, y: 332, d: 11, status: "live" },
  K("mg1-freq", 605, 404, 44, "FREQUENCY", { status: "live", bind: "mg1Rate" }),

  // ---- MODULATION GENERATOR 2 (KLM-63 MOD-VCA)
  { kind: "lamp", id: "mg2-lamp", x: 704, y: 332, d: 11, status: "live" },
  K("mg2-freq", 700, 404, 44, "FREQUENCY ∿", { status: "live", bind: "mg2Rate" }),

  // ---- GENERAL ENVELOPE GENERATOR (KLM-76 geg + trigger)
  SEL("kbd-trigger", 697, 214, 44, "KBD TRIGGER SELECT",
    ["OFF", "1", "2", "3", "4", "5"],
    { status: "soon", bind: "kbdTrigger",
      note: "trigger.dsp possel ladder; instrument_poly exposes only SINGLE/MULTIPLE (phase 4)" }),
  K("geg-delay", 782, 208, 44, "DELAY ⌒", { status: "live", bind: "gegDelay" }),
  K("geg-attack", 782, 294, 44, "ATTACK ⌒", { status: "live", bind: "gegAttack" }),
  K("geg-release", 782, 382, 44, "RELEASE ⌒", { status: "live", bind: "gegRelease" }),
  SW("geg-auto", 765, 428, "AUTO", ["", "ON"], { status: "inert", note: "no board model" }),
  SW("geg-shape", 800, 428, "", ["", "ON"], { status: "inert",
    note: "trapezoid polarity - no board model" }),

  // ---- VOLTAGE PROCESSORS (KLM-76 vp)
  // The panel carries LIMITER A and LIMITER B per processor. vp.dsp models
  // the LMA/LMB sections as one DUAL-GANG pot per channel, so only one knob
  // per processor is currently reachable; A is bound, B is pending the
  // netlist re-read (see the plan's open questions).
  K("vp1-lim-a", 878, 213, 40, "LIMITER A", { scale: "bipolar", status: "live", bind: "vp1" }),
  K("vp1-lim-b", 878, 288, 40, "LIMITER B", { scale: "bipolar", status: "soon", bind: "vp1b",
    note: "vp.dsp gangs LMA/LMB to one knob - verify against klm76-vp.cir" }),
  K("vp2-lim-a", 878, 362, 40, "LIMITER A", { scale: "bipolar", status: "live", bind: "vp2" }),
  K("vp2-lim-b", 878, 434, 40, "LIMITER B", { scale: "bipolar", status: "soon", bind: "vp2b",
    note: "vp.dsp gangs LMA/LMB to one knob - verify against klm76-vp.cir" }),
];

// ---- patch field ---------------------------------------------------------
// Every jack the panel silkscreens, with the voltage range printed beneath it.
// `role` drives the cord engine: "src" jacks emit a CV, "dst" jacks consume
// one, "mult" jacks are the passive JUNCTION bus.
const J = (id, x, y, label, role, range, o = {}) =>
  ({ kind: "jack", id, x, y, label, role, range, status: "inert", ...o });

export const JACKS = [
  // destination inputs, under the board boxes
  J("j-freq", 977, 202, "FREQ", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstFreq" }),
  J("j-pwm", 1002, 202, "PWM", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstPwm" }),
  J("j-cutoff", 1039, 202, "CUTOFF FREQ", "dst", "-5V", { status: "soon", bind: "dstCutoff" }),
  J("j-attack", 1085, 202, "ATTACK", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstAttack" }),
  J("j-release", 1118, 202, "RELEASE", "dst", "\u2192GND", { status: "soon", bind: "dstRelease" }),
  J("j-peak", 1156, 202, "PEAK FREQ", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstPeak" }),
  J("j-vca1", 1318, 202, "", "dst", "0V\u2192+5V", { status: "soon", bind: "dstVca1" }),
  J("j-vca2", 1352, 202, "", "dst", "0V\u2192+5V", { status: "soon", bind: "dstVca2" }),
  // instrument outputs
  J("j-final-out", 1395, 157, "FINAL\nOUT", "src", "", { status: "panel", labelSide: "right" }),
  J("j-phones", 1395, 202, "PHONES", "src", "", { status: "panel", labelSide: "right" }),
  J("j-direct-out", 1395, 240, "DIRECT\nOUT", "src", "", { status: "panel", labelSide: "right" }),
  // modulation sources
  J("j-mg1-out", 972, 278, "MG 1 OUT", "src", "5VP-P", { status: "soon", bind: "srcMg1" }),
  J("j-sh-out", 1045, 278, "S/H OUT", "src", "", { status: "soon", bind: "srcSh" }),
  J("j-mg2-out", 1156, 278, "MG 2 OUT", "src", "5VP-P", { status: "soon", bind: "srcMg2" }),
  J("j-geg-out-1", 1215, 278, "GEG OUT", "src", "", { status: "soon", bind: "srcGeg1" }),
  J("j-geg-out-2", 1270, 278, "GEG OUT", "src", "", { status: "soon", bind: "srcGeg2" }),
  J("j-vp1-out", 1331, 278, "OUT", "src", "", { status: "soon", bind: "srcVp1" }),
  J("j-vp2-out", 1387, 278, "OUT", "src", "", { status: "soon", bind: "srcVp2" }),
  // module inputs
  J("j-vca-in", 972, 366, "", "dst", "0V\u2192+5V", { status: "soon", bind: "dstVcaMod" }),
  J("j-sh-in", 1044, 366, "S/H IN", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstShIn" }),
  J("j-mg1-freq", 1100, 366, "FREQ", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstMg1Freq" }),
  J("j-trig-in", 1281, 366, "TRIG IN", "dst", "\u2192GND", { status: "soon", bind: "dstTrigIn" }),
  J("j-vp1-in", 1329, 366, "INPUT", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstVp1In" }),
  J("j-vp2-in", 1387, 366, "INPUT", "dst", "-5V\u2192+5V", { status: "soon", bind: "dstVp2In" }),
  // JUNCTION passive multiple, keyboard trigger outs, pedal jacks
  J("j-junction-1", 972, 410, "", "mult", "", { status: "soon", bind: "mult" }),
  J("j-junction-2", 1000, 410, "", "mult", "", { status: "soon", bind: "mult" }),
  J("j-junction-3", 1026, 410, "", "mult", "", { status: "soon", bind: "mult" }),
  J("j-junction-4", 1055, 410, "", "mult", "", { status: "soon", bind: "mult" }),
  J("j-trig-single", 1158, 410, "TRIG OUT\nSINGLE", "src", "\u2192GND",
    { status: "soon", bind: "srcTrigSingle" }),
  J("j-trig-multiple", 1189, 410, "TRIG OUT\nMULTIPLE", "src", "\u2192GND",
    { status: "soon", bind: "srcTrigMultiple" }),
  J("j-pedal", 1239, 410, "", "dst", "-5V", { status: "inert" }),
  J("j-foot", 1277, 410, "", "dst", "\u2192GND", { status: "inert" }),
];

// DIN sockets and the labelled block-diagram boxes drawn in the patch field.
export const PATCH_BOXES = [
  { id: "pb-siggen", label: "SIGNAL\nGENERATORS", x: 968, y: 160, w: 44, h: 20 },
  { id: "pb-dlpf", label: "DYNAMIC\nLP\nFILTERS", x: 1024, y: 160, w: 44, h: 20 },
  { id: "pb-envmod", label: "ENVELOPE\nMODIFIERS", x: 1080, y: 160, w: 46, h: 20 },
  { id: "pb-reson", label: "RESONATORS", x: 1138, y: 160, w: 46, h: 20 },
  { id: "pb-am", label: "AMPLITUDE\nMODULATOR", x: 1196, y: 160, w: 46, h: 20 },
  { id: "pb-ensemble", label: "ENSEMBLE", x: 1254, y: 160, w: 44, h: 20 },
  { id: "pb-vca", label: "VCA", x: 962, y: 316, w: 28, h: 18, shape: "tri" },
  { id: "pb-sh", label: "SAMPLE\n& HOLD", x: 1025, y: 316, w: 34, h: 20 },
  { id: "pb-mg1", label: "MODULATION\nGENERATOR\n1", x: 1081, y: 316, w: 36, h: 22 },
  { id: "pb-mg2", label: "MODULATION\nGENERATOR\n2", x: 1137, y: 316, w: 36, h: 22 },
  { id: "pb-keyboard", label: "KEYBOARD", x: 1158, y: 359, w: 44, h: 17 },
  { id: "pb-geg", label: "GENERAL\nENVELOPE\nGENERATOR", x: 1220, y: 316, w: 44, h: 22 },
  { id: "pb-vp1", label: "VOLTAGE\nPROCESSOR\n1", x: 1312, y: 316, w: 40, h: 22 },
  { id: "pb-vp2", label: "VOLTAGE\nPROCESSOR\n2", x: 1366, y: 316, w: 40, h: 22 },
];

export const VCA_TRIANGLES = [
  { id: "tri-vca1", label: "VCA 1", x: 826, y: 78, w: 28, h: 20 },
  { id: "tri-vca2", label: "VCA 2", x: 920, y: 78, w: 28, h: 20 },
];

export const DINS = [
  { id: "din-1", label: "CONTROLLER", x: 1325, y: 410, d: 26 },
  { id: "din-2", label: "CONTROLLER", x: 1383, y: 410, d: 26 },
];

// ---- silkscreen flow graphics -------------------------------------------
// The white signal-flow lines. `bus` lines are the two long horizontals the
// orange taps sit on; `flow` is the TOTAL SIGNAL MODIFIERS chain.
export const FLOW = {
  buses: [
    { id: "bus-left", x1: 112, y1: 326, x2: 568, y2: 326 },
    { id: "bus-right", x1: 568, y1: 150, x2: 1465, y2: 150 },
  ],
  lines: [
    // AM -> ENSEMBLE -> VCA1 -> VCA2 -> KBD VOL BAL -> FINAL VOLUME
    { x1: 641, y1: 82, x2: 716, y2: 82 },
    { x1: 760, y1: 82, x2: 824, y2: 82 },
    { x1: 856, y1: 82, x2: 918, y2: 82 },
    { x1: 950, y1: 82, x2: 984, y2: 82 },
    { x1: 1042, y1: 82, x2: 1091, y2: 82 },
    // taps drop from the chain to the bus
    { x1: 826, y1: 98, x2: 826, y2: 150 },
    { x1: 920, y1: 98, x2: 920, y2: 150 },
    { x1: 633, y1: 110, x2: 633, y2: 150 },
  ],
  boxes: [
    { id: "fb-ensemble", label: "ENSEMBLE", x: 716, y: 82, w: 44, h: 24 },
  ],
};

// free silkscreen text (section names printed in the field, not over a box)
export const TEXTS = [
  { text: "MODULATION\nGENERATOR 1", x: 605, y: 292 },
  { text: "MODULATION\nGENERATOR 2", x: 702, y: 292 },
  { text: "JUNCTION", x: 1013, y: 386, size: 4.4 },
  { text: "1", x: 832, y: 250, size: 5 },
  { text: "2", x: 832, y: 398, size: 5 },
];

export const STATUS_LEGEND = [
  ["live", "bound to a SPICE-refereed board model"],
  ["soon", "board is modeled; wiring not done yet"],
  ["panel", "real panel function, no circuit model (KLM-77 etc.)"],
  ["inert", "not modeled anywhere - never faked"],
];
