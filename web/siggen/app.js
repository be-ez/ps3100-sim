// KLM-64E signal generator bench. One note card, keybed-addressed the way
// the key matrix addresses the real ones: note index 0..11 (F..E) plus
// octave row. The card free-runs, so a JS gain gate stands in for the
// GEG/VCA downstream.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const PARAM = {
  note: "/siggen/note",
  octave: "/siggen/octave",
  cv: "/siggen/cv",
  wfd: "/siggen/wfd",
  wfr: "/siggen/wfr",
};

const KEY_LO = 41, KEY_HI = 76; // F2..E5 -> rows 3..1
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

let audioContext = null;
let node = null;
let analyser = null;
let gateGain = null;
let building = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "siggen", "./generated");

  gateGain = new GainNode(audioContext, { gain: 0 });   // bench gate
  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const master = new GainNode(audioContext, { gain: 0.06 });  // volts -> speaker
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  node.connect(gateGain);
  gateGain.connect(analyser);
  analyser.connect(master);
  master.connect(limiter);
  limiter.connect(audioContext.destination);

  pushAllParams();
}

async function powerOn() {
  if (!building) building = buildAudio();
  await building;
  if (audioContext.state !== "running") await audioContext.resume();
  power.set(true);
  $("power-lamp").classList.add("on");
}

async function powerToggle(wantOn) {
  if (wantOn || !audioContext || audioContext.state !== "running") return powerOn();
  await audioContext.suspend();
  power.set(false);
  $("power-lamp").classList.remove("on");
}

// ---------- panel state ----------

const state = { wfd: 0, wfr: 1.0, vibOn: false, vRate: 0.5, vDepth: 0.3, freq: 0.873 };

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

let vibPhase = 0;

function pushAllParams() {
  setParam(PARAM.wfd, state.wfd * 13);   // WFD rail 0..13 V (0 = saw)
  setParam(PARAM.wfr, state.wfr * 14.9); // WFR rail
  setParam(PARAM.cv, cvVolts());         // temperament bus, volts
}

// temperament bus: -9..-0.55 V, 0.93 V/oct; 0.873 = neutral -1.62 V
const cvVolts = () => -9 + 8.45 * state.freq;
makeKnob($("knob-wfd"), state.wfd, (v) => { state.wfd = v; setParam(PARAM.wfd, v * 13); });
makeKnob($("knob-wfr"), state.wfr, (v) => { state.wfr = v; setParam(PARAM.wfr, v * 14.9); });
makeKnob($("knob-vrate"), state.vRate, (v) => { state.vRate = v; });
makeKnob($("knob-vdepth"), state.vDepth, (v) => { state.vDepth = v; });

makeRocker($("vib-on"), false, (on) => {
  state.vibOn = on;
  if (!on) setParam(PARAM.cv, cvVolts());
});

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed: (note, octave row) addressing, mono, JS gate ----------

const held = [];

function applyKey(midi) {
  // note index: 0=F .. 11=E (card stuffing order); F2..E5 -> rows 3..1
  setParam(PARAM.note, (midi - 41) % 12);
  setParam(PARAM.octave, 3 - Math.floor((midi - 41) / 12));
}

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (!held.includes(midi)) held.push(midi);
  applyKey(midi);
  gateGain.gain.setTargetAtTime(1, audioContext.currentTime, 0.005);
}

function noteOff(midi) {
  const i = held.indexOf(midi);
  if (i >= 0) held.splice(i, 1);
  if (held.length) applyKey(held[held.length - 1]);
  else gateGain.gain.setTargetAtTime(0, audioContext.currentTime, 0.05);
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope + vibrato ----------

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  onFrame(dt) {
    if (state.vibOn && audioContext && audioContext.state === "running") {
      vibPhase += 2 * Math.PI * (2 + 6 * state.vRate) * dt;   // 2..8 Hz
      // FM pin is ~21.6 V/oct: +-0.2 V is a musical vibrato at full depth
      setParam(PARAM.cv, cvVolts() + 0.08 * state.vDepth * Math.sin(vibPhase));
    }
  },
});
