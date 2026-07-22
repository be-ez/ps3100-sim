// KLM-76 GEG bench. The wasm outputs the 0..+5 V trapezoid CV itself; it is
// drawn on the strip-chart and patched in JS onto a saw drone's gain - the
// GEG OUT -> VCA CONT normal. Keys supply the gate (and the drone pitch).
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { stripScope } from "../lib/scope.js";

const PARAM = {
  gate: "/geg/gate",
  delay: "/geg/delay",
  attack: "/geg/attack",
  release: "/geg/release",
};

const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

let audioContext = null;
let gegNode = null;
let envAnalyser = null;
let drone = null;
let droneVca = null;
let building = null;
let envData = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  gegNode = await loadFaustNode(audioContext, "geg", "./generated");

  // envelope CV is observed, not heard
  envAnalyser = new AnalyserNode(audioContext, { fftSize: 512 });
  gegNode.connect(envAnalyser);
  const pull = new GainNode(audioContext, { gain: 0 });   // analyser must reach the destination to render
  envAnalyser.connect(pull);
  pull.connect(audioContext.destination);

  // the patched voice: saw drone through a JS "VCA" driven by the CV
  drone = new OscillatorNode(audioContext, { type: "sawtooth", frequency: 110 });
  droneVca = new GainNode(audioContext, { gain: 0 });
  const master = new GainNode(audioContext, { gain: 0.25 * state.level });
  masterRef = master;
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  drone.connect(droneVca);
  droneVca.connect(master);
  master.connect(limiter);
  limiter.connect(audioContext.destination);
  drone.start();

  pushAllParams();
}

let masterRef = null;

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

// ---------- panel state (pots are CW = slow, like the hardware) ----------

const state = { delay: 0.0, attack: 0.3, release: 0.3, level: 0.7 };

function setParam(addr, v) { if (gegNode) gegNode.setParamValue(addr, v); }

function pushAllParams() {
  setParam(PARAM.delay, state.delay);
  setParam(PARAM.attack, state.attack);
  setParam(PARAM.release, 1 - state.release);  // traced sense: krel=1 fast
}

makeKnob($("knob-delay"), state.delay, (v) => { state.delay = v; setParam(PARAM.delay, v); });
makeKnob($("knob-attack"), state.attack, (v) => { state.attack = v; setParam(PARAM.attack, v); });
makeKnob($("knob-release"), state.release, (v) => { state.release = v; setParam(PARAM.release, 1 - v); });  // traced sense: krel=1 fast
makeKnob($("knob-level"), state.level, (v) => {
  state.level = v;
  if (masterRef) masterRef.gain.setTargetAtTime(0.25 * v, audioContext.currentTime, 0.02);
});

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed: gate + drone pitch ----------

const held = [];

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (!held.includes(midi)) held.push(midi);
  drone.frequency.setTargetAtTime(440 * 2 ** ((midi - 69) / 12), audioContext.currentTime, 0.004);
  setParam(PARAM.gate, 1);
}

function noteOff(midi) {
  const i = held.indexOf(midi);
  if (i >= 0) held.splice(i, 1);
  if (held.length) {
    drone.frequency.setTargetAtTime(
      440 * 2 ** ((held[held.length - 1] - 69) / 12), audioContext.currentTime, 0.004);
  } else {
    setParam(PARAM.gate, 0);
  }
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope + lamp + CV patch ----------

function envVolts() {
  if (!envAnalyser || audioContext.state !== "running") return null;
  if (!envData) envData = new Float32Array(envAnalyser.fftSize);
  envAnalyser.getFloatTimeDomainData(envData);
  return envData[envData.length - 1];
}

const lampEl = $("env-lamp");

stripScope($("scope"), envVolts, {
  min: -0.5, max: 6.3, seconds: 10,
  onFrame() {
    const v = envVolts();
    // GEG OUT -> VCA CONT: 0..5 V opens the drone
    const g = v === null ? 0 : Math.max(0, Math.min(1, v / 5.87));
    if (droneVca) droneVca.gain.setTargetAtTime(g, audioContext.currentTime, 0.005);
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
  },
});
