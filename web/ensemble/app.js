// KLM-76 Ensemble bench. Polyphonic saw pads through the wasm BBD chorus;
// the two lamps breathe at the channels' SPICE-measured LFO rates.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const PARAM = { bypass: "/ensemble/bypass", g1: "/ensemble/g1", g2: "/ensemble/g2" };

// SPICE-measured CD4069 ring LFO rates (dsp/ensemble.dsp lfoFA/lfoFB)
const LFO_A_HZ = 3.0599, LFO_B_HZ = 2.5391;

const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

// ---------- audio graph ----------

let audioContext = null;
let ensNode = null;
let analyser = null;
let voiceBus = null;
let building = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  ensNode = await loadFaustNode(audioContext, "ensemble", "./generated");

  voiceBus = new GainNode(audioContext, { gain: 0.25 });
  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  voiceBus.connect(ensNode);
  ensNode.connect(analyser);
  analyser.connect(limiter);
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

const state = { on: true, g1: 1.0, g2: 1.0 };

function setParam(addr, v) { if (ensNode) ensNode.setParamValue(addr, v); }

function pushAllParams() {
  setParam(PARAM.bypass, state.on ? 0 : 1);
  setParam(PARAM.g1, state.g1);
  setParam(PARAM.g2, state.g2);
}

makeKnob($("knob-g1"), state.g1, (v) => { state.g1 = v; setParam(PARAM.g1, v); });
makeKnob($("knob-g2"), state.g2, (v) => { state.g2 = v; setParam(PARAM.g2, v); });

makeRocker($("ens-on"), true, (on) => {
  state.on = on;
  setParam(PARAM.bypass, on ? 0 : 1);
});

const power = makeRocker($("power"), false, powerToggle);

// ---------- voices: soft polyphonic saw pads ----------

const voices = new Map();

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (voices.has(midi)) return;
  const t = audioContext.currentTime;
  const osc = new OscillatorNode(audioContext,
    { type: "sawtooth", frequency: 440 * 2 ** ((midi - 69) / 12) });
  const env = new GainNode(audioContext, { gain: 0 });
  env.gain.setTargetAtTime(1, t, 0.06);            // pad-like swell
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
  v.env.gain.setTargetAtTime(0, t, 0.25);          // slow pad release
  v.osc.stop(t + 2.0);
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope + LFO lamps ----------

const lampEls = [$("lfoa"), $("lfob")];
const lampHz = [LFO_A_HZ, LFO_B_HZ];
const lampPhase = [0, 0.5];

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  onFrame(dt) {
    const running = audioContext && audioContext.state === "running" && state.on;
    lampEls.forEach((el, i) => {
      if (running) lampPhase[i] = (lampPhase[i] + lampHz[i] * dt) % 1;
      const g = running ? 0.5 + 0.5 * Math.sin(2 * Math.PI * lampPhase[i]) : 0;
      el.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
      el.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
    });
  },
});
