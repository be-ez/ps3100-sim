// KLM-63 MOD-VCA + MG2 bench. The wasm board has two outputs: ch0 audio
// through the vactrol VCA, ch1 the MG2 triangle. The triangle is drawn on
// the scope and can be patched onto MOD VCA CONT in JS - the front-panel
// pin-jack patch.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { stripScope } from "../lib/scope.js";

const PARAM = { cv: "/modvca/vca_cv", rate: "/modvca/mg2_rate" };
const MG2_AMP = 2.73; // triangle volts at pin 27 (dsp/modvca.dsp)

const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

// ---------- audio graph ----------

let audioContext = null;
let node = null;
let mg2Analyser = null;
let voiceBus = null;
let building = null;
let mg2Data = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "modvca", "./generated");

  voiceBus = new GainNode(audioContext, { gain: 2 });   // "volts" into the VCA
  const splitter = new ChannelSplitterNode(audioContext, { numberOfOutputs: 2 });
  mg2Analyser = new AnalyserNode(audioContext, { fftSize: 512 });
  const master = new GainNode(audioContext, { gain: 0.15 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  voiceBus.connect(node);
  node.connect(splitter);
  splitter.connect(master, 0);          // audio
  splitter.connect(mg2Analyser, 1);     // MG2 triangle
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

const state = { cv: 0.7, rate: 0.5, depth: 0.6, patch: false };

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

function mg2Volts() {
  if (!mg2Analyser || audioContext.state !== "running") return null;
  if (!mg2Data) mg2Data = new Float32Array(mg2Analyser.fftSize);
  mg2Analyser.getFloatTimeDomainData(mg2Data);
  return mg2Data[mg2Data.length - 1];
}

const cvNow = () => {
  if (!state.patch) return state.cv;
  const v = mg2Volts();
  if (v === null) return state.cv;
  return Math.min(1, Math.max(0, state.cv + state.depth * 0.5 * (v / MG2_AMP)));
};

function pushAllParams() {
  setParam(PARAM.cv, cvNow());
  setParam(PARAM.rate, state.rate);
}

makeKnob($("knob-cv"), state.cv, (v) => { state.cv = v; setParam(PARAM.cv, cvNow()); });
makeKnob($("knob-rate"), state.rate, (v) => { state.rate = v; setParam(PARAM.rate, v); });
makeKnob($("knob-depth"), state.depth, (v) => { state.depth = v; });

makeRocker($("patch-on"), false, (on) => {
  state.patch = on;
  if (!on) setParam(PARAM.cv, state.cv);
});

const power = makeRocker($("power"), false, powerToggle);

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

// ---------- scope (MG2 triangle) + vactrol lamp ----------

const lampEl = $("vac1");
let lampGlow = 0;

stripScope($("scope"), mg2Volts, {
  min: -3.2, max: 3.2, seconds: 6,
  onFrame(dt) {
    // live patch: MG2 -> CONT at control rate; the vactrol model in the
    // wasm supplies the lag
    if (state.patch && audioContext && audioContext.state === "running") {
      setParam(PARAM.cv, cvNow());
    }
    const drive = (audioContext && audioContext.state === "running") ? cvNow() : 0;
    const tau = drive > lampGlow ? 0.02 : 0.25;
    lampGlow += (drive - lampGlow) * Math.min(1, dt / tau);
    const g = lampGlow;
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
  },
});
