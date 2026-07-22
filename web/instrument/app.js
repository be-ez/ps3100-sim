// Composed instrument bench: one wasm (dsp/instrument.dsp), all boards
// inside. The GEG->VCA envelope wire lives in the DSP; JS only sets panel
// params and the keybed's (note, octave, gate).
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const P = "/instrument/";
const PARAM = {
  wfd: `${P}siggen/wfd`,
  note: `${P}siggen/note`,
  octave: `${P}siggen/octave`,
  vfc: `${P}gate/vfc`,
  attack: `${P}vca/geg/attack`,
  release: `${P}vca/geg/release`,
  gate: `${P}vca/geg/gate`,
  cv2: `${P}vca/cv2`,
  rescv: `${P}resonator/cv`,
  blend: `${P}resonator/blend`,
  bypass: `${P}ensemble/bypass`,
};

const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

let audioContext = null;
let node = null;
let analyser = null;
let building = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "instrument", "./generated");

  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const master = new GainNode(audioContext, { gain: 0.12 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  node.connect(analyser);
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

const state = { wfd: 0, vfc: 0.6, attack: 0.15, release: 0.3, cv: 0.5, blend: 0.7, ensOn: true };

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

const vfcVolts = (k) => -14 + 14 * k;   // /instrument/gate/vfc range

function pushAllParams() {
  setParam(PARAM.wfd, state.wfd * 13);
  setParam(PARAM.vfc, vfcVolts(state.vfc));
  setParam(PARAM.attack, state.attack);
  setParam(PARAM.release, 1 - state.release);  // traced sense: krel=1 fast
  setParam(PARAM.cv2, 1);
  setParam(PARAM.rescv, state.cv);
  setParam(PARAM.blend, state.blend);
  setParam(PARAM.bypass, state.ensOn ? 0 : 1);
}

makeKnob($("knob-wfd"), state.wfd, (v) => { state.wfd = v; setParam(PARAM.wfd, v * 13); });
makeKnob($("knob-vfc"), state.vfc, (v) => { state.vfc = v; setParam(PARAM.vfc, vfcVolts(v)); });
makeKnob($("knob-attack"), state.attack, (v) => { state.attack = v; setParam(PARAM.attack, v); });
makeKnob($("knob-release"), state.release, (v) => { state.release = v; setParam(PARAM.release, 1 - v); });  // traced sense: krel=1 fast
makeKnob($("knob-cv"), state.cv, (v) => { state.cv = v; setParam(PARAM.rescv, v); });
makeKnob($("knob-blend"), state.blend, (v) => { state.blend = v; setParam(PARAM.blend, v); });

makeRocker($("ens-on"), true, (on) => {
  state.ensOn = on;
  setParam(PARAM.bypass, on ? 0 : 1);
});

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed ----------

const held = [];
let envGlow = 0;

function applyKey(midi) {
  setParam(PARAM.note, (midi - 41) % 12);
  setParam(PARAM.octave, 3 - Math.floor((midi - 41) / 12));
}

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (!held.includes(midi)) held.push(midi);
  applyKey(midi);
  setParam(PARAM.gate, 1);
}

function noteOff(midi) {
  const i = held.indexOf(midi);
  if (i >= 0) held.splice(i, 1);
  if (held.length) applyKey(held[held.length - 1]);
  else setParam(PARAM.gate, 0);
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope + envelope lamp (visual approximation of the GEG) ----------

const lampEl = $("env-lamp");

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  onFrame(dt) {
    // the envelope lives inside the wasm; the lamp mirrors gate state with
    // attack/release-shaped glow so the panel still breathes
    const gateOn = held.length > 0 && audioContext && audioContext.state === "running";
    const tau = gateOn ? 0.02 + 0.4 * state.attack : 0.05 + 0.6 * state.release;
    envGlow += ((gateOn ? 1 : 0) - envGlow) * Math.min(1, dt / tau);
    const g = envGlow;
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
  },
});
