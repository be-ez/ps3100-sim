// KLM-63 MG1 + noise bench. A source board: no keybed. The LFO pins are
// sub-audio, shown on the strip-chart; the noise pins go to the speakers
// with a spectrum scope.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, makeChipGroup } from "../lib/panel.js";
import { stripScope, spectrumScope } from "../lib/scope.js";

const PARAM = {
  vfc1: "/mg1_noise/vfc1",
  fadj: "/mg1_noise/fadj",
  ngain: "/mg1_noise/noise_gain",
  outsel: "/mg1_noise/outsel",
};

const $ = (id) => document.getElementById(id);

// ---------- audio graph ----------

let audioContext = null;
let node = null;
let analyser = null;
let speakerGain = null;
let building = null;
let timeData = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "mg1_noise", "./generated");

  // worklets with unconnected inputs are treated as inactive and render
  // silence; a running silent source on the input keeps them processing
  const keepAlive = new ConstantSourceNode(audioContext, { offset: 0 });
  keepAlive.start();
  keepAlive.connect(node);

  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.8 });
  speakerGain = new GainNode(audioContext, { gain: 0 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  node.connect(analyser);
  analyser.connect(speakerGain);
  speakerGain.connect(limiter);
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

const state = { freq: 0.5, fadj: 0.5, ngain: 0.5, outsel: 0 };

const isNoise = () => state.outsel >= 4;

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

function routeAudio() {
  if (!speakerGain) return;
  speakerGain.gain.setTargetAtTime(isNoise() ? 0.6 : 0, audioContext.currentTime, 0.02);
  $("scope-strip").classList.toggle("scope-hidden", isNoise());
  $("scope-spec").classList.toggle("scope-hidden", !isNoise());
}

function pushAllParams() {
  setParam(PARAM.vfc1, (state.freq - 0.5) * 6);   // +-3 V ~ +-5 octaves
  setParam(PARAM.fadj, state.fadj);
  setParam(PARAM.ngain, state.ngain);
  setParam(PARAM.outsel, state.outsel);
  routeAudio();
}

makeKnob($("knob-freq"), state.freq, (v) => { state.freq = v; setParam(PARAM.vfc1, (v - 0.5) * 6); });
makeKnob($("knob-fadj"), state.fadj, (v) => { state.fadj = v; setParam(PARAM.fadj, v); });
makeKnob($("knob-ngain"), state.ngain, (v) => { state.ngain = v; setParam(PARAM.ngain, v); });

makeChipGroup(document.querySelector(".chips"), (v) => {
  state.outsel = Number(v);
  setParam(PARAM.outsel, state.outsel);
  routeAudio();
});

const power = makeRocker($("power"), false, powerToggle);
document.querySelector(".panel").addEventListener("pointerdown", () => {
  if (!audioContext) powerOn();
}, { once: true });

// ---------- scopes + lamp ----------

function lastSample() {
  if (!analyser || audioContext.state !== "running") return null;
  if (!timeData) timeData = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(timeData);
  return timeData[timeData.length - 1];
}

const lampEl = $("mg1-lamp");

stripScope($("scope-strip"), () => (isNoise() ? null : lastSample()), {
  min: -4, max: 4, seconds: 4,
  onFrame(dt) {
    // lamp follows the LFO (positive half glows), like an MG rate lamp
    const v = isNoise() ? null : lastSample();
    const g = v === null ? 0 : Math.max(0, Math.min(1, 0.5 + v / 6));
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
  },
});

spectrumScope($("scope-spec"), () => (isNoise() ? analyser : null), () => audioContext.sampleRate);
