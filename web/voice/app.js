// Full voice chain: every stage is its board's SPICE-validated wasm, chained
// through Web Audio (interim wiring until the composed instrument.dsp).
//   siggen -> VCA (cv1 driven by the GEG's trapezoid, read in JS) ->
//   resonator -> ensemble -> out.
// Mono: one note card, like soloing one key of the real 48.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

let audioContext = null;
let nodes = null;      // { siggen, geg, vca, resonator, ensemble }
let envAnalyser = null;
let outAnalyser = null;
let building = null;
let envData = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });

  const [siggen, geg, vca, resonator, ensemble] = await Promise.all([
    loadFaustNode(audioContext, "siggen", "../siggen/generated"),
    loadFaustNode(audioContext, "geg", "../geg/generated"),
    loadFaustNode(audioContext, "vca", "../vca/generated"),
    loadFaustNode(audioContext, "resonator", "../resonator/generated"),
    loadFaustNode(audioContext, "ensemble", "../ensemble/generated"),
  ]);
  nodes = { siggen, geg, vca, resonator, ensemble };

  // audio path
  const intoResonator = new GainNode(audioContext, { gain: 0.5 }); // volts trim
  outAnalyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const master = new GainNode(audioContext, { gain: 0.1 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  siggen.connect(vca);
  vca.connect(intoResonator);
  intoResonator.connect(resonator);
  resonator.connect(ensemble);
  ensemble.connect(outAnalyser);
  outAnalyser.connect(master);
  master.connect(limiter);
  limiter.connect(audioContext.destination);

  // control path: GEG CV observed in JS, patched onto VCA CONT each frame
  envAnalyser = new AnalyserNode(audioContext, { fftSize: 512 });
  geg.connect(envAnalyser);
  const pull = new GainNode(audioContext, { gain: 0 });   // analyser must reach the destination to render
  envAnalyser.connect(pull);
  pull.connect(audioContext.destination);

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

const state = { wfd: 0, attack: 0.15, release: 0.3, cv: 0.5, blend: 0.7, ensOn: true };

const set = (n, addr, v) => { if (nodes) nodes[n].setParamValue(addr, v); };

function pushAllParams() {
  set("siggen", "/siggen/wfd", state.wfd * 13);
  set("geg", "/geg/delay", 0);
  set("geg", "/geg/attack", state.attack);
  set("geg", "/geg/release", 1 - state.release);  // traced sense: krel=1 fast
  set("vca", "/vca/cv1", 0);
  set("vca", "/vca/cv2", 1);
  set("resonator", "/resonator/cv", state.cv);
  set("resonator", "/resonator/blend", state.blend);
  set("ensemble", "/ensemble/bypass", state.ensOn ? 0 : 1);
}

makeKnob($("knob-wfd"), state.wfd, (v) => { state.wfd = v; set("siggen", "/siggen/wfd", v * 13); });
makeKnob($("knob-attack"), state.attack, (v) => { state.attack = v; set("geg", "/geg/attack", v); });
makeKnob($("knob-release"), state.release, (v) => { state.release = v; set("geg", "/geg/release", 1 - v); });
makeKnob($("knob-cv"), state.cv, (v) => { state.cv = v; set("resonator", "/resonator/cv", v); });
makeKnob($("knob-blend"), state.blend, (v) => { state.blend = v; set("resonator", "/resonator/blend", v); });

makeRocker($("ens-on"), true, (on) => {
  state.ensOn = on;
  set("ensemble", "/ensemble/bypass", on ? 0 : 1);
});

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed: mono, addresses the note card + gates the GEG ----------

const held = [];

function applyKey(midi) {
  set("siggen", "/siggen/note", (midi - 41) % 12);
  set("siggen", "/siggen/octave", 3 - Math.floor((midi - 41) / 12));
}

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (!held.includes(midi)) held.push(midi);
  applyKey(midi);
  set("geg", "/geg/gate", 1);
}

function noteOff(midi) {
  const i = held.indexOf(midi);
  if (i >= 0) held.splice(i, 1);
  if (held.length) applyKey(held[held.length - 1]);
  else set("geg", "/geg/gate", 0);
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope + GEG->VCA patch ----------

function envVolts() {
  if (!envAnalyser || audioContext.state !== "running") return null;
  if (!envData) envData = new Float32Array(envAnalyser.fftSize);
  envAnalyser.getFloatTimeDomainData(envData);
  return envData[envData.length - 1];
}

const lampEl = $("env-lamp");

spectrumScope($("scope"), () => outAnalyser, () => audioContext.sampleRate, {
  onFrame() {
    const v = envVolts();
    const g = v === null ? 0 : Math.max(0, Math.min(1, v / 5));
    set("vca", "/vca/cv1", g);   // GEG OUT -> VCA 1 CONT; vactrol adds its lag
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
  },
});
