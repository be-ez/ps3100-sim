// KLM-69E gate channel bench. A saw voice feeds the wasm channel; the keys
// drive the channel's own gate (CD4007 pass-gate + RC envelope), CUTOFF is
// the FC bus voltage, DRIVE scales the input so the KORG35 core's junction
// saturation becomes audible.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const PARAM = {
  vfc: "/gate/vfc",
  expand: "/gate/expand",
  gate: "/gate/gate",
  attack: "/gate/attack",
  release: "/gate/release",
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
let osc = null;
let driveGain = null;
let building = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "gate", "./generated");

  // mono saw at "core volts": the cell clips from a few mV, so DRIVE spans
  // clean (~1 mV) to crunchy (~50 mV)
  osc = new OscillatorNode(audioContext, { type: "sawtooth", frequency: 110 });
  driveGain = new GainNode(audioContext, { gain: driveVolts(state.drive) });
  osc.connect(driveGain);
  driveGain.connect(node);
  osc.start();

  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const master = new GainNode(audioContext, { gain: 0 });
  masterRef = master;
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  node.connect(analyser);
  analyser.connect(master);
  master.connect(limiter);
  limiter.connect(audioContext.destination);

  pushAllParams();
}

let masterRef = null;

async function powerOn() {
  if (!building) building = buildAudio();
  await building;
  if (audioContext.state !== "running") await audioContext.resume();
  power.set(true);
  $("power-lamp").classList.add("on");
  updateMaster();
}

async function powerToggle(wantOn) {
  if (wantOn || !audioContext || audioContext.state !== "running") return powerOn();
  await audioContext.suspend();
  power.set(false);
  $("power-lamp").classList.remove("on");
}

// ---------- panel state ----------

const state = { vfc: 0.6, drive: 0.4, attack: 0.15, release: 0.2, expand: 0 };

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

const vfcVolts = (k) => -14 + 14 * k;
const driveVolts = (k) => 0.001 * (50 ** k);          // 1 mV .. 50 mV peak
const attackSec = (k) => 0.001 * (1000 ** k);         // 1 ms .. 1 s
const releaseSec = (k) => 0.05 * (200 ** k);          // 50 ms .. 10 s

function updateMaster() {
  // keep loudness roughly level as DRIVE rises (output tracks input below clip)
  if (masterRef) masterRef.gain.setTargetAtTime(
    0.4 * (0.004 / driveVolts(state.drive)) ** 0.7, audioContext.currentTime, 0.03);
}

function pushAllParams() {
  setParam(PARAM.vfc, vfcVolts(state.vfc));
  setParam(PARAM.expand, state.expand);
  setParam(PARAM.attack, attackSec(state.attack));
  setParam(PARAM.release, releaseSec(state.release));
}

makeKnob($("knob-vfc"), state.vfc, (v) => { state.vfc = v; setParam(PARAM.vfc, vfcVolts(v)); });
makeKnob($("knob-expand"), state.expand, (v) => { state.expand = v; setParam(PARAM.expand, v); });
makeKnob($("knob-drive"), state.drive, (v) => {
  state.drive = v;
  if (driveGain) driveGain.gain.setTargetAtTime(driveVolts(v), audioContext.currentTime, 0.02);
  updateMaster();
});
makeKnob($("knob-attack"), state.attack, (v) => { state.attack = v; setParam(PARAM.attack, attackSec(v)); });
makeKnob($("knob-release"), state.release, (v) => { state.release = v; setParam(PARAM.release, releaseSec(v)); });

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed: mono pitch + channel gate ----------

const held = [];
let envGlow = 0;

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (!held.includes(midi)) held.push(midi);
  osc.frequency.setTargetAtTime(440 * 2 ** ((midi - 69) / 12), audioContext.currentTime, 0.004);
  setParam(PARAM.gate, 1);
}

function noteOff(midi) {
  const i = held.indexOf(midi);
  if (i >= 0) held.splice(i, 1);
  if (held.length) {
    osc.frequency.setTargetAtTime(
      440 * 2 ** ((held[held.length - 1] - 69) / 12), audioContext.currentTime, 0.004);
  } else {
    setParam(PARAM.gate, 0);
  }
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope + envelope lamp ----------

const lampEl = $("env-lamp");

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  onFrame(dt) {
    const gateOn = held.length > 0 && audioContext && audioContext.state === "running";
    const tau = gateOn ? 0.01 + attackSec(state.attack) : releaseSec(state.release) * 0.5;
    envGlow += ((gateOn ? 1 : 0) - envGlow) * Math.min(1, dt / tau);
    const g = envGlow;
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
  },
});
