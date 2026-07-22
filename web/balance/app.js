// KLM-62D balance/AM bench. Keyboard modes route the saw keybed into the
// upper or lower channel of the balance mixer; TWO-TONE uses the board's
// internal carrier + AM MOD IN pair so the JFET cell's sidebands are
// visible on the spectrum scope.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, makeChipGroup, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const P = "/balance_am/";
const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

let audioContext = null;
let node = null;
let analyser = null;
let voiceBus = null;
let building = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "balance_am", "./generated");

  voiceBus = new GainNode(audioContext, { gain: 0.5 });
  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const master = new GainNode(audioContext, { gain: 0.5 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  voiceBus.connect(node);
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

const state = { bal: 0.5, intensity: 1.0, fmod: 0.4, source: "upper" };

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

const fmodHz = (k) => 30 * (100 ** k);   // 30 Hz .. 3 kHz

function pushAllParams() {
  setParam(`${P}bal`, state.bal);
  setParam(`${P}intensity`, state.intensity);
  setParam(`${P}fmod_hz`, fmodHz(state.fmod));
  applySource();
}

function applySource() {
  const tt = state.source === "ttones";
  setParam(`${P}ttones`, tt ? 1 : 0);
  setParam(`${P}input_sel`, state.source === "lower" ? 1 : 0);
  // audible two-tone levels (the DSP defaults are SPICE-referee levels)
  setParam(`${P}fcar`, 2000);
  setParam(`${P}acar`, tt ? 1.0 : 0.02);
  setParam(`${P}amod`, tt ? 2.0 : 0);
}

makeKnob($("knob-bal"), state.bal, (v) => { state.bal = v; setParam(`${P}bal`, v); });
makeKnob($("knob-int"), state.intensity, (v) => { state.intensity = v; setParam(`${P}intensity`, v); });
makeKnob($("knob-fmod"), state.fmod, (v) => { state.fmod = v; setParam(`${P}fmod_hz`, fmodHz(v)); });

makeChipGroup(document.querySelector(".chips"), (v) => { state.source = v; applySource(); });

const power = makeRocker($("power"), false, powerToggle);
document.querySelector(".panel").addEventListener("pointerdown", () => {
  if (!audioContext) powerOn();
}, { once: true });

// ---------- voices ----------

const voices = new Map();

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (voices.has(midi)) return;
  const t = audioContext.currentTime;
  const osc = new OscillatorNode(audioContext,
    { type: "sawtooth", frequency: 440 * 2 ** ((midi - 69) / 12) });
  const env = new GainNode(audioContext, { gain: 0 });
  env.gain.setTargetAtTime(1, t, 0.004);
  osc.connect(env);
  env.connect(voiceBus);
  osc.start(t);
  voices.set(midi, { osc, env });
}

function noteOff(midi) {
  const v = voices.get(midi);
  if (!v) return;
  voices.delete(midi);
  const t = audioContext.currentTime;
  v.env.gain.cancelScheduledValues(t);
  v.env.gain.setTargetAtTime(0, t, 0.09);
  v.osc.stop(t + 0.8);
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope ----------

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate);
