// PS-3100 full panel: the composed 48-channel instrument wasm plus the
// modulation boards as live wasm nodes, patched onto the instrument's buses
// at control rate - the browser stands in for the panel pin-jacks:
//   MG1 tri -> VP1 (real attenuverter wasm) -> dest
//   MG2 tri (from the KLM-63 MOD-VCA board) -> depth -> dest
//   S&H (self-clocked noise steps) -> VP2 -> dest
//
// The panel field itself is generated from layout.js, which holds the traced
// geometry of every control the real instrument carries. Each control's
// `status` says how it is backed; see the header of layout.js. Nothing that
// has no circuit model is wired to anything - it renders inert.
import { loadFaustNode } from "../lib/faust-loader.js";
import {
  makeKnob, makeRocker, makeSelector, makeSlideSwitch, buildKeybed,
} from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";
import {
  PANEL, SECTIONS, SCALES, CONTROLS, JACKS, PATCH_BOXES, VCA_TRIANGLES,
  DINS, FLOW, TEXTS, STATUS_LEGEND,
} from "./layout.js";
import { PARAM, CTL, CTL_CH } from "./params.js";

// The real keyboard: 48 notes, F2..E6 - exactly the 12 pitch classes x 4
// octave rows poly.dsp hardwires. Every key is its own permanent channel.
const KEY_LO = 41, KEY_HI = 88;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

// MG 1 selector position -> mg1_noise outsel. Square/pink/white are
// unambiguous. The three shape positions are PROVISIONAL: the board's pins
// are 34 (triangle), 35 (its inversion) and 37 (the full-wave-rectified
// triangle, i.e. a symmetric triangle at 2x rate), but the panel sheet that
// wires the selector to those pins is absent from the repo - the same gap
// klm63-mg1-noise.cir's header records for the FREQ CONT pots. Resolve
// against that sheet before treating the shape positions as faithful.
const MG1_OUTSEL = [3, 1, 0, 2, 5, 4];

const $ = (id) => document.getElementById(id);

// ---------- panel state ----------
// Knobs/trims hold 0..1; selectors, slides and bus taps hold a position index.

const state = {};

const attackSec = (k) => 0.001 * (1000 ** k);

// ---- panel knob -> board pin volts -------------------------------------
// The conditioning boards take pin voltages; the panel pot wiring that sets
// them is cross-board and absent from the repo (dsp/filterctl.dsp and
// dsp/freqctl.dsp both say so and expose the pins directly), so the sweeps
// below are stated assumptions, not transcriptions.

// FINE / COARSE land on freqctl pins 40 / 39 over their full +-14.9 V range.
// The board's own R-ratios do the rest: COARSE enters the summer at -0.122
// and FINE at -0.0213, so one turn of COARSE is ~1.95 octaves and one of
// FINE ~0.34, without either law being invented here.
const pinVolts = (k) => (k - 0.5) * 2 * 14.9;

// CUTOFF FREQUENCY sweeps filterctl pin 5 far enough to take IC1a clip to
// clip at the factory FC ADJ (ofs ~ +6.77 V, so vo1 = ofs - vfc covers
// +-13 V over vfc = -6.2 .. +19.8). Knob 0.657 reproduces the netlist's own
// default operating point, Vfcu = -0.137 V (gate.dsp vfc = -6, f0 ~ 1.6 kHz).
const cutoffVolts = (k) => -6.23 + k * 26.0;

// PULSE WIDTH MODULATION INTENSITY drives the PWM IN pin over the range
// wavectl's affine law was fitted on (-5 .. +6 V).
const pwmVolts = (k) => -5.0 + k * 11.0;

// filterctl bus volts -> gate.dsp's vfc slider units. gate.dsp maps its
// slider LINEARLY onto the physical open-circuit bus, vfcu = 0.12 + vfc *
// (0.6/14), so this is that mapping inverted. poly.dsp shares ONE FC bus
// across all 48 channels - its header calls it "the same blended FCU/FCL
// bus" - so the two halves are averaged into it. Splitting them by octave
// row is the phase-4 work that makes KBD FILTER BALANCE mean anything.
const busToVfc = (fcu, fcl) => ((fcu + fcl) / 2 - 0.12) * (14.0 / 0.6);

// Release terminal volts -> the per-key release RC in seconds. Log-linear
// through the anchors the SPICE referee pins in
// tests/test_gate_spice.py::test_release_bus_sets_release_rate:
// +0.14 V ~20 ms (damped), +5.8..+8.0 V tens of ms (half damp), +11.61 V
// 4.7 s (full release, the R401 4.7M RC). NB poly.dsp's release slider
// floors at 0.05 s, so the damped state lands there rather than at 20 ms.
const REL_ANCHORS = [[0.14, 0.02], [5.8, 0.045], [8.0, 0.07], [11.61, 4.7]];
function relSeconds(v) {
  const a = REL_ANCHORS;
  if (v <= a[0][0]) return a[0][1];
  for (let i = 1; i < a.length; i++) {
    if (v > a[i][0]) continue;
    const t = (v - a[i - 1][0]) / (a[i][0] - a[i - 1][0]);
    return Math.exp(Math.log(a[i - 1][1]) * (1 - t) + Math.log(a[i][1]) * t);
  }
  return a[a.length - 1][1];
}

let audioContext = null, inst = null, mods = null, analyser = null, building = null;
let outGain = null;
const setI = (addr, v) => { if (inst) inst.setParamValue(addr, v); };

// ---------- binding table ----------
// board: what the control actually reaches, shown in the hover readout.
// init:  the control's power-up position.
// set:   push the control's value at whatever owns it.
const nop = () => {};
const BIND = {
  // --- KLM-64E / poly core
  // --- conditioning boards, through the composed panelctl wasm
  cutoff: { board: "KLM-63 filterctl vfc -> FCU/FCL -> poly FC bus", init: 0.657,
    set: (v) => setC(CTL.vfc, cutoffVolts(v)) },
  waveform: { board: "KLM-63 wavectl selector -> WFR/WFD rails", init: 1,
    set: (i) => { setC(CTL.wave, i); setC(CTL.pwmOn, i === 5 ? 1 : 0); } },
  pwmInt: { board: "KLM-63 wavectl PWM IN pin", init: 0.45,
    set: (v) => setC(CTL.pwmDc, pwmVolts(v)) },
  fine: { board: "KLM-62D freqctl FINE (pin 40)", init: 0.5,
    set: (v) => setC(CTL.fine, pinVolts(v)) },
  coarse: { board: "KLM-62D freqctl COARSE (pin 39)", init: 0.5,
    set: (v) => setC(CTL.coarse, pinVolts(v)) },
  // three-detent switch: 0 RELEASE (pin 6 grounded), 1 HALF D, 2 DAMPED
  releaseMode: { board: "KLM-62D relctl -> gate release terminal", init: 0,
    set: (i) => { setC(CTL.relSw, i === 0 ? 1 : 0); setC(CTL.hdSw, i === 1 ? 1 : 0); } },

  attack: { board: "KLM-69E gate - per-key attack RC", init: 0.25,
    set: (v) => setI(PARAM.attack, attackSec(v)) },

  // --- KLM-62 resonators
  blend: { board: "KLM-62 resonator blend", init: 0.7, set: (v) => setI(PARAM.blend, v) },
  peak1: { board: "KLM-62 resonator band 1", init: 0.5, set: (v) => setI(PARAM.peak1, v) },
  peak2: { board: "KLM-62 resonator band 2", init: 0.5, set: (v) => setI(PARAM.peak2, v) },
  peak3: { board: "KLM-62 resonator band 3", init: 0.5, set: (v) => setI(PARAM.peak3, v) },
  mg2ToRes: { board: "MG 2 -> resonator sweep bus", init: 0, set: nop },
  extToRes: { board: "external peak-freq modulation depth", init: 0.7, set: nop },

  // --- KLM-76 GEG + VCA + S&H + VP
  gegDelay: { board: "KLM-76 GEG delay", init: 0, set: (v) => setI(PARAM.gegDelay, v) },
  gegAttack: { board: "KLM-76 GEG attack", init: 0.1, set: (v) => setI(PARAM.gegAttack, v) },
  // traced sense: krel = 1 is fast
  gegRelease: { board: "KLM-76 GEG release", init: 0.35,
    set: (v) => setI(PARAM.gegRelease, 1 - v) },
  vca1Tap: { board: "GEG OUT normalled to VCA 1 CONT", init: 1, set: nop },
  vca2Tap: { board: "KLM-76 VCA 2 CONT", init: 1, set: (v) => setI(PARAM.cv2, v ? 1 : 0) },
  shClock: { board: "KLM-76 S&H clock", init: 0.5,
    set: (v) => mods?.sh.setParamValue("/sh/clock", v) },
  vp1: { board: "KLM-76 voltage processor 1", init: 0.85,
    set: (v) => mods?.vp.setParamValue("/vp/knob1", v) },
  vp2: { board: "KLM-76 voltage processor 2", init: 0.85,
    set: (v) => mods?.vp.setParamValue("/vp/knob2", v) },

  // --- KLM-63 modulation generators
  mg1Wave: { board: "KLM-63 MG 1 / noise output select", init: 2,
    set: (i) => mods?.mg1.setParamValue("/mg1_noise/outsel", MG1_OUTSEL[i] ?? 0) },
  mg1Rate: { board: "KLM-63 MG 1 rate (FREQ CONT I)", init: 0.5,
    set: (v) => mods?.mg1.setParamValue("/mg1_noise/vfc1", (v - 0.5) * 6) },
  mg2Rate: { board: "KLM-63 MOD-VCA / MG 2 rate", init: 0.5,
    set: (v) => mods?.mg2.setParamValue("/modvca/mg2_rate", v) },

  // --- KLM-76 ensemble
  ensemble: { board: "KLM-76 ensemble bypass", init: 1,
    set: (v) => setI(PARAM.bypass, v ? 0 : 1) },

  // --- panel-level routing (real controls, JS law; the KLM-62D/63
  //     conditioning boards land in phase 2)
  mg1ToFreq: { board: "MG 1 -> temperament bus (panel-level)", init: 0, set: nop },
  gegToFreq: { board: "GEG/EXT -> temperament bus (panel-level)", init: 0, set: nop },
  mg1ToCutoff: { board: "MG 1 -> FC bus (panel-level)", init: 0, set: nop },
  gegToCutoff: { board: "GEG/EXT -> FC bus (panel-level)", init: 0, set: nop },
  fmTap: { board: "frequency-modulation bus tap", init: 0, set: nop },
  reverse: { board: "modulation polarity (freqctl MOD-R pins)", init: 0, set: nop },
  cutoffModTap: { board: "cutoff-modulation bus tap", init: 0, set: nop },
  cutoffOnTap: { board: "cutoff-modulation bus ON", init: 0, set: nop },
  scale: { board: "octave transpose (keyboard wiring)", init: 0.5, set: nop },

  // --- output stage: KLM-77 is not modeled, these are Web Audio gains
  finalVolume: { board: "output gain (KLM-77 not modeled)", init: 0.55,
    set: (v) => { if (outGain) outGain.gain.value = 0.32 * v * v; } },
  phoneVolume: { board: "output gain (KLM-77 not modeled)", init: 0.6, set: nop },
  directVolume: { board: "output gain (KLM-77 not modeled)", init: 0.6, set: nop },

  power: { board: "", init: 0, set: nop },
};

// panelctl parameter push (null until the audio graph is built)
function setC(addr, v) { if (mods?.ctl) mods.ctl.setParamValue(addr, v); }

// ---------- render the silkscreen field from layout.js ----------

const pan = $("pan");

function mk(cls, x, y, parent = pan) {
  const e = document.createElement("div");
  e.className = cls;
  e.style.left = `${x}px`;
  e.style.top = `${y}px`;
  parent.appendChild(e);
  return e;
}

function label(cls, text, x, y, side) {
  if (!text) return null;
  const e = mk(cls, x, y);
  e.textContent = text;
  e.style.transform = side === "left"
    ? "translate(-100%, -50%)"
    : side === "right" ? "translate(0, -50%)"
    : side === "below" ? "translate(-50%, 0)"
    : "translate(-50%, -100%)";
  if (side === "left") e.style.textAlign = "right";
  return e;
}

// section boxes + their legends
SECTIONS.forEach((s, i) => {
  const box = mk(s.inset ? "sec sec-inset" : "sec", s.x, s.y);
  box.style.width = `${s.w}px`;
  box.style.height = `${s.h}px`;
  if (i === 0) box.dataset.first = "1";
  if (!s.title) return;
  const tx = s.titleAt === "left" ? s.x + 62 : s.x + s.w / 2;
  const t = label("sec-title", s.title, tx, s.y + 7, "below");
  // a legend wider than its column is shrunk rather than allowed to bleed
  // into the neighbouring section (TEMPERAMENT ADJUST, GENERAL ENVELOPE
  // GENERATOR); the real silkscreen sets these in a smaller face too.
  const fit = (s.w - 6) / t.offsetWidth;
  if (fit < 1) t.style.fontSize = `${Math.max(3.6, 6.2 * fit)}px`;
});

// flow graphics
for (const b of FLOW.buses) {
  const e = mk("flow-line flow-bus", b.x1, b.y1 - 1);
  e.style.width = `${b.x2 - b.x1}px`;
}
for (const l of FLOW.lines) {
  const horiz = l.y1 === l.y2;
  const e = mk("flow-line", Math.min(l.x1, l.x2), Math.min(l.y1, l.y2));
  e.style.width = `${horiz ? Math.abs(l.x2 - l.x1) : 1}px`;
  e.style.height = `${horiz ? 1 : Math.abs(l.y2 - l.y1)}px`;
}
for (const b of FLOW.boxes) {
  const e = mk("fbox", b.x, b.y - b.h / 2);
  e.style.width = `${b.w}px`;
  e.style.height = `${b.h}px`;
  e.innerHTML = `<span>${b.label}</span>`;
}
for (const t of VCA_TRIANGLES) {
  const e = mk("ftri", t.x - t.w / 2, t.y - t.h / 2);
  e.style.width = `${t.w}px`;
  e.style.height = `${t.h}px`;
  e.innerHTML = `<span>${t.label}</span>`;
}
for (const b of PATCH_BOXES) {
  const e = mk(b.shape === "tri" ? "ftri" : "pbox", b.x, b.y - b.h / 2);
  e.style.width = `${b.w}px`;
  e.style.height = `${b.h}px`;
  e.innerHTML = `<span>${b.label}</span>`;
}
for (const d of DINS) {
  mk("pdin", d.x, d.y);
  label("jack-label", d.label, d.x, d.y + 17, "below");
}

for (const t of TEXTS) {
  const e = label("ctl-label", t.text, t.x, t.y, "below");
  if (t.size) e.style.fontSize = `${t.size}px`;
}

// knob skirt numerals
function drawScale(c) {
  const set = SCALES[c.scale];
  if (!set || c.d < 40) return;
  const r = c.d / 2 + 7;
  set.forEach((txt, i) => {
    const a = (-135 + i * 27) * Math.PI / 180;
    const e = mk("pk-scale", c.x + r * Math.sin(a), c.y - r * Math.cos(a));
    e.textContent = txt;
  });
}

// selector position legends around the sweep
function drawPositions(c) {
  const n = c.positions.length;
  const r = c.d / 2 + 11;
  c.positions.forEach((txt, i) => {
    if (!txt) return;
    const a = (-135 + i * (270 / (n - 1))) * Math.PI / 180;
    const e = mk("psel-pos", c.x + r * Math.sin(a), c.y - r * Math.cos(a));
    e.textContent = txt;
    e.dataset.for = c.id;
    e.dataset.idx = String(i);
  });
}

const widgets = {};

function renderControl(c) {
  let el;
  const lines = (c.label || "").split("\n").length;

  if (c.kind === "knob" || c.kind === "trim") {
    el = mk(c.kind === "trim" ? "pk pk-trim" : "pk", c.x, c.y);
    el.style.setProperty("--kd", `${c.d}px`);
    el.tabIndex = 0;
    el.setAttribute("role", "slider");
    el.setAttribute("aria-label", (c.label || c.id).replace(/\n/g, " "));
    drawScale(c);
  } else if (c.kind === "selector") {
    el = mk("pk psel", c.x, c.y);
    el.style.setProperty("--kd", `${c.d}px`);
    el.tabIndex = 0;
    el.setAttribute("role", "slider");
    el.setAttribute("aria-label", (c.label || c.id).replace(/\n/g, " "));
    drawPositions(c);
  } else if (c.kind === "slide" || c.kind === "bustap") {
    el = mk(c.kind === "slide" ? "pslide" : "ptap", c.x, c.y);
    el.dataset.count = String(c.positions.length);
    el.tabIndex = 0;
    el.setAttribute("role", "slider");
    el.setAttribute("aria-label", (c.label || c.id).replace(/\n/g, " "));
    el.innerHTML = `<span class="${c.kind === "slide" ? "pslide-cap" : "ptap-cap"}"></span>`;
    // each detent's legend printed beside its position, top = last index
    const n = c.positions.length;
    const h = c.kind === "slide" ? 22 : 26;
    c.positions.forEach((txt, i) => {
      if (!txt) return;
      const ly = c.y + h / 2 - 3 - (i / (n - 1)) * (h - 6);
      label("ctl-label", txt, c.x + 8, ly, "right");
    });
  } else if (c.kind === "lamp") {
    el = mk("plamp", c.x, c.y);
    el.style.setProperty("--d", `${c.d}px`);
  } else if (c.kind === "power") {
    el = mk("ppower", c.x, c.y);
    el.setAttribute("aria-pressed", "false");
  }
  if (!el) return;

  el.id = c.id;
  el.dataset.status = c.status;
  if (c.note) el.dataset.note = c.note;
  if (c.board) el.dataset.board = c.board;

  // label placement: to the left for the intensity-control knobs, else above
  if (c.label) {
    const side = c.labelSide === "left" ? "left" : "above";
    const clear = c.kind === "selector" ? 19 : c.scale && c.d >= 40 ? 11 : 5;
    const lx = side === "left" ? c.x - c.d / 2 - 7 : c.x;
    const ly = side === "left" ? c.y : c.y - (c.d ?? 20) / 2 - clear;
    label("ctl-label", c.label, lx, ly, side);
  }
  widgets[c.id] = el;
}

CONTROLS.forEach(renderControl);

// patch jacks
for (const j of JACKS) {
  const el = mk("pjack", j.x, j.y);
  el.id = j.id;
  el.dataset.status = j.status;
  el.dataset.role = j.role;
  el.tabIndex = 0;
  el.setAttribute("aria-label", `${(j.label || j.id).replace(/\n/g, " ")} jack`);
  if (j.label) {
    label("jack-label", j.label,
      j.labelSide === "right" ? j.x + 16 : j.x,
      j.labelSide === "right" ? j.y : j.y - 16,
      j.labelSide === "right" ? "right" : "above");
  }
  if (j.range) label("jack-range", j.range, j.x, j.y + 16, "below");
}

// ---------- wire controls to the binding table ----------

function bindControl(c) {
  const el = widgets[c.id];
  if (!el) return;
  const b = c.bind ? BIND[c.bind] : null;

  if (c.kind === "power") {
    power = makeRocker(el, false, powerToggle);
    return;
  }
  if (c.kind === "lamp") return;

  const live = !!b;
  const init = b ? b.init : (c.kind === "knob" || c.kind === "trim" ? 0.5 : 0);
  state[c.bind ?? c.id] = init;

  const onChange = (v) => {
    state[c.bind ?? c.id] = v;
    if (live) b.set(v);
  };

  if (c.kind === "knob" || c.kind === "trim") {
    makeKnob(el, init, onChange);
  } else if (c.kind === "selector") {
    makeSelector(el, c.positions.length, init, (i) => {
      onChange(i);
      pan.querySelectorAll(`.psel-pos[data-for="${c.id}"]`).forEach((p) => {
        p.dataset.on = p.dataset.idx === String(i) ? "1" : "0";
      });
    });
    pan.querySelectorAll(`.psel-pos[data-for="${c.id}"]`).forEach((p) => {
      p.dataset.on = p.dataset.idx === String(init) ? "1" : "0";
    });
  } else if (c.kind === "slide" || c.kind === "bustap") {
    makeSlideSwitch(el, c.positions.length, init, onChange);
  }
}

let power = null;
CONTROLS.forEach(bindControl);

// ---------- hover readout + the modelled/not-modelled x-ray ----------

const STATUS_TEXT = {
  live: "LIVE", soon: "MODELED - NOT WIRED YET",
  panel: "PANEL-LEVEL (no circuit model)", inert: "NOT MODELED",
};
const readout = $("readout");
const describe = (el) => {
  const c = CONTROLS.find((x) => x.id === el.id) ?? JACKS.find((x) => x.id === el.id);
  if (!c) return "";
  const b = c.bind ? BIND[c.bind] : null;
  const name = (c.label || c.id).replace(/\n/g, " ");
  const where = b?.board || c.note || "";
  return `<b>${name}</b> &middot; ${STATUS_TEXT[c.status]}${where ? ` &middot; ${where}` : ""}`;
};
pan.addEventListener("pointerover", (e) => {
  const t = e.target.closest("[data-status]");
  if (t) readout.innerHTML = describe(t);
});
pan.addEventListener("pointerout", (e) => {
  if (!e.relatedTarget || !pan.contains(e.relatedTarget)) readout.innerHTML = "";
});

const legend = $("legend");
legend.innerHTML =
  STATUS_LEGEND.map(([k, text]) =>
    `<span><i class="legend-key" data-k="${k}"></i><b>${k}</b> ${text}</span>`).join("") +
  `<button class="xray-toggle" id="xray" aria-pressed="false">SHOW WHAT'S MODELED</button>`;
$("xray").addEventListener("click", (e) => {
  const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
  e.currentTarget.setAttribute("aria-pressed", String(on));
  pan.dataset.xray = on ? "1" : "0";
});

// ---------- scale the field to the viewport ----------

const frame = $("panel-frame");
function fit() {
  const s = frame.clientWidth / PANEL.w;
  pan.style.transform = `scale(${s})`;
  frame.style.height = `${PANEL.h * s}px`;
}
new ResizeObserver(fit).observe(frame);
fit();

// ---------- audio ----------

function audioBadge(text) {
  let el = $("audio-badge");
  if (!el) {
    el = document.createElement("span");
    el.id = "audio-badge";
    el.className = "legend-sm";
    el.style.color = "#e04c3a";
    document.querySelector(".power-block")?.appendChild(el);
  }
  el.textContent = text;
}

// Chrome's audio renderer can die (device switch, e.g. bluetooth handoff, or
// renderer crash) - resume() never recovers such a context, only a fresh one
// does. But closing a HEALTHY context with many live worklets can itself
// crash the tab, so: normal power toggling suspends/resumes, and the full
// teardown+rebuild runs ONLY when the context is detected dead.
let audioDead = false;
function markDead(why) {
  if (audioDead) return;
  audioDead = true;
  console.warn("audio renderer lost:", why);
  audioBadge("AUDIO LOST — flip POWER off/on");
}
function watchAudioHealth(ctx, nodes) {
  ctx.onstatechange = () => {
    if (ctx.state === "interrupted") markDead("context " + ctx.state);
  };
  for (const n of nodes) {
    if (n && "onprocessorerror" in n) {
      n.onprocessorerror = (e) => { console.error(e); markDead("processor error"); };
    }
  }
}

function warnIfSpFallback() {
  if (typeof window === "undefined" || !window.__spFallback) return;
  const el = document.createElement("span");
  el.className = "legend-sm";
  el.style.color = "#e04c3a";
  el.textContent = "SP MODE — use https/localhost";
  document.querySelector(".power-block")?.appendChild(el);
}

let cvSink = null;   // zero-gain pull: analyser subgraphs must reach the
                     // destination or Chrome never renders them
function tapCv(node, channel = null, width = 2) {
  if (!cvSink) {
    cvSink = new GainNode(audioContext, { gain: 0 });
    cvSink.connect(audioContext.destination);
  }
  const an = new AnalyserNode(audioContext, { fftSize: 512 });
  an.connect(cvSink);
  if (channel !== null) {         // pick ONE channel; plain connect down-mixes
    const split = new ChannelSplitterNode(audioContext, { numberOfOutputs: width });
    node.connect(split);
    split.connect(an, channel);
  } else {
    node.connect(an);
  }
  const buf = new Float32Array(an.fftSize);
  return () => { an.getFloatTimeDomainData(buf); return buf[buf.length - 1]; };
}

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });

  const [instrument, mg1, mg2, sh, vp, ctl] = await Promise.all([
    loadFaustNode(audioContext, "instrument_poly", "../poly/generated"),
    loadFaustNode(audioContext, "mg1_noise", "../mg1noise/generated"),
    loadFaustNode(audioContext, "modvca", "../modvca/generated"),
    loadFaustNode(audioContext, "sh", "../sh/generated"),
    loadFaustNode(audioContext, "vp", "../vp/generated"),
    loadFaustNode(audioContext, "panelctl", "../panelctl/generated"),
  ]);
  inst = instrument;

  // audio path: only the instrument reaches the speakers
  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  outGain = new GainNode(audioContext, { gain: 0.1 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });
  inst.connect(analyser);
  analyser.connect(outGain);
  outGain.connect(limiter);
  limiter.connect(audioContext.destination);

  // worklets with unconnected inputs are treated as inactive and render
  // silence; a running silent source on the input keeps them processing
  const keepAlive = new ConstantSourceNode(audioContext, { offset: 0 });
  keepAlive.start();
  keepAlive.connect(mg1);
  keepAlive.connect(mg2);
  keepAlive.connect(vp);   // one input, channels are internal to the worklet
  keepAlive.connect(ctl);  // PWM IN / FC MOD pins; the patch field feeds these

  mg2.setParamValue("/modvca/probe", 3);              // MG2 triangle on ch0
  sh.setParamValue("/sh/testmode", 0);                // external in = noise
  (() => {                                             // noise -> S&H input
    const len = 2 * audioContext.sampleRate;
    const b = audioContext.createBuffer(1, len, audioContext.sampleRate);
    const d = b.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = 8 * (Math.random() - 0.5);
    const src = new AudioBufferSourceNode(audioContext, { buffer: b, loop: true });
    src.connect(sh);
    src.start();
  })();

  watchAudioHealth(audioContext, [inst, mg1, mg2, sh, vp, ctl]);

  // the conditioning boards' six pins, read back at frame rate: all four are
  // DC/sub-audio boards (the slowest corner in any of them is wavectl's 47 ms
  // rail release), so a ~16 ms poll resolves every one of their dynamics
  const ctlVal = Object.fromEntries(
    Object.entries(CTL_CH).map(([k, ch]) => [k, tapCv(ctl, ch, 6)]));

  mods = {
    mg1, mg2, sh, vp, ctl, ctlVal,
    mg1Val: tapCv(mg1),
    mg2Val: tapCv(mg2),
    shVal: tapCv(sh),
    vp1Val: tapCv(vp, 0),   // out1 (monitor=0 -> y1)
    vp2Val: tapCv(vp, 1),   // out2 = y2
  };
  vp.setParamValue("/vp/monitor", 0);

  pushAllParams();
}

// push every bound control's current position at its owner
function pushAllParams() {
  setI(PARAM.rescv, 0.5);   // base; modulation adds per frame
  setI(PARAM.multiple, 0);
  // cvTune, vfc, release, wfr and wfd are no longer pushed from here: they
  // are read back off panelctl's pins every frame (see the scope onFrame).
  for (const c of CONTROLS) {
    const b = c.bind ? BIND[c.bind] : null;
    if (b && b.set !== nop) b.set(state[c.bind]);
  }
}

async function powerOn() {
  if (audioDead || audioContext?.state === "closed") {   // rebuild path
    try { await audioContext?.close(); } catch {}
    audioContext = null; inst = null; mods = null; analyser = null; building = null;
    cvSink = null; outGain = null;
    audioDead = false;
    $("audio-badge")?.remove();
  }
  if (!building) building = buildAudio();
  await building;
  if (audioContext.state !== "running") {
    await audioContext.resume();
    // a resume that never reaches "running" means the renderer is gone
    await new Promise((ok) => setTimeout(ok, 300));
    if (audioContext.state !== "running") { markDead("resume stalled"); return; }
  }
  power.set(true);
  $("power-lamp").classList.add("on");
  warnIfSpFallback();
}

async function powerToggle(wantOn) {
  if (wantOn || !audioContext || audioContext.state !== "running") return powerOn();
  // full teardown: a fresh context on next power-on recovers from renderer
  // death and re-acquires the current output device
  try { await audioContext.close(); } catch {}
  audioContext = null; inst = null; mods = null; analyser = null; building = null;
  cvSink = null; outGain = null;
  $("audio-badge")?.remove();
  power.set(false);
  $("power-lamp").classList.remove("on");
}

// ---------- keybed -> bitmask ----------

const held = new Set();

function pushKeys() {
  let lo = 0, hi = 0;
  for (const midi of held) {
    const pc = (midi - KEY_LO) % 12;
    const oct = 3 - Math.floor((midi - KEY_LO) / 12);
    if (oct < 0 || oct > 3) continue;
    const bit = pc * 4 + oct;
    if (bit < 24) lo |= 1 << bit;
    else hi |= 1 << (bit - 24);
  }
  setI(PARAM.keysLo, lo >>> 0);
  setI(PARAM.keysHi, hi >>> 0);
  setI(PARAM.nkeys, held.size);
}

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  held.add(midi);
  pushKeys();
}
function noteOff(midi) { held.delete(midi); pushKeys(); }

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- the patch field: CVs -> instrument buses, per frame ----------

const lampGlow = (el, g) => {
  if (!el) return;
  el.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
  el.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
};

const modSum = { sweep: 0, pitch: 0, cutoff: 0 };
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

window.__cvDebug = () => {
  let outRms = null;
  if (analyser) {
    const b = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(b);
    outRms = Math.sqrt(b.reduce((s2, v) => s2 + v * v, 0) / b.length);
  }
  const inst_ = {}, ctl_ = {};
  if (inst) for (const [k, a] of Object.entries(PARAM)) inst_[k] = inst.getParamValue(a);
  if (mods?.ctl) for (const [k, a] of Object.entries(CTL)) ctl_[k] = mods.ctl.getParamValue(a);
  const pins_ = {};
  if (mods?.ctlVal) for (const [k, f] of Object.entries(mods.ctlVal)) pins_[k] = f();
  return mods && {
    mg1: mods.mg1Val(), mg2: mods.mg2Val(), sh: mods.shVal(),
    vp1: mods.vp1Val(), vp2: mods.vp2Val(),
    outRms, ctxState: audioContext?.state, nkeys: held.size, inst: inst_, ctl: ctl_, pins: pins_,
  };
};

// Dominant-frequency probe: parabolic interpolation of the FFT peak, so a
// few cents of wobble is resolvable well below the 11.7 Hz bin width. Used to
// tell real modulation (ensemble, MG) apart from any jitter the control-rate
// readback might introduce.
window.__pitchHz = () => {
  if (!analyser) return null;
  const n = analyser.frequencyBinCount;
  const db = new Float32Array(n);
  analyser.getFloatFrequencyData(db);
  let k = 1;
  for (let i = 2; i < n - 1; i++) if (db[i] > db[k]) k = i;
  const d = 0.5 * (db[k - 1] - db[k + 1]) / (db[k - 1] - 2 * db[k] + db[k + 1] || 1);
  return (k + (Number.isFinite(d) ? d : 0)) * audioContext.sampleRate / analyser.fftSize;
};

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  onFrame() {
    if (!mods || !audioContext || audioContext.state !== "running") return;

    // MG1 tri (+-3.3 V) and S&H (+-~5 V) run through the real VP wasm
    mods.vp.setParamValue("/vp/vin1", mods.mg1Val() ?? 0);
    mods.vp.setParamValue("/vp/vin2", mods.shVal() ?? 0);
    const src = {
      mg1: (mods.vp1Val() ?? 0) / 3.5,          // VP1-processed MG1, ~unit
      mg2: (mods.mg2Val() ?? 0) / 2.73,
      sh: (mods.vp2Val() ?? 0) / 5.5,           // VP2-processed S&H
    };
    lampGlow(widgets["mg1-lamp"], Math.min(1, Math.abs(src.mg1)));
    lampGlow(widgets["mg2-lamp"], Math.min(1, Math.abs(src.mg2)));

    // the panel's own intensity controls decide where MG1/GEG land; the
    // PEAK FREQUENCY MODULATION BY MG 2 switch gates MG2 onto the sweep bus
    const sgn = state.reverse ? -1 : 1;
    modSum.pitch = state.fmTap
      ? sgn * src.mg1 * (state.mg1ToFreq ?? 0) : 0;
    modSum.cutoff = state.cutoffOnTap
      ? src.mg1 * (state.mg1ToCutoff ?? 0) + src.sh * (state.gegToCutoff ?? 0) : 0;
    modSum.sweep = state.mg2ToRes ? src.mg2 * (state.extToRes ?? 0) : 0;

    setI(PARAM.rescv, Math.min(1, Math.max(0, 0.5 + 0.4 * modSum.sweep)));

    // ---- conditioning boards -> the instrument's buses ----
    // Every value below is a VOLTAGE AT A BOARD PIN computed by a
    // SPICE-refereed model, not a panel curve invented here.
    const c = mods.ctlVal;
    // KLM-63 WAVE FORM rails -> the signal generators' shaper rails
    setI(PARAM.wfr, clamp(c.wfr(), 0, 14.9));
    setI(PARAM.wfd, clamp(c.wfd(), 0, 13));
    // KLM-62D temperament bus -> the master pitch CV (same bus, same units);
    // panel-level pitch modulation still sums on top until the patch field
    // reaches freqctl's own MOD pins
    setI(PARAM.cvTune, clamp(c.bus() + 0.25 * modSum.pitch, -9, -0.55));
    // KLM-63 FCU/FCL -> the shared KORG35 cutoff bus
    setI(PARAM.vfc, clamp(busToVfc(c.fcu(), c.fcl()) + 4 * modSum.cutoff, -14, 0));
    // KLM-62D gate release terminal -> the per-key release RC
    setI(PARAM.release, clamp(relSeconds(c.rel()), 0.05, 10));
  },
});
