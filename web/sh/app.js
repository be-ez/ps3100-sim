// KLM-76 sample & hold bench. The wasm S&H (dsp/sh.dsp) holds a stepped
// voltage; here it is patched the classic way - S/H OUT into a VCO's pitch
// input - so the staircase is audible as random stepped pitch. The S&H
// output itself is a CV and never reaches the speakers directly.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, makeChipGroup } from "../lib/panel.js";
import { stripScope } from "../lib/scope.js";

const PARAM = {
  clock: "/sh/clock",
  droop: "/sh/droop",
  testmode: "/sh/testmode",
  sineHz: "/sh/sine_hz",
  rampSlope: "/sh/ramp_slope",
};

const TESTMODE = { noise: 0, ramp: 1, sine: 2 };  // noise = external input

const $ = (id) => document.getElementById(id);

// ---------- audio graph ----------

let audioContext = null;
let shNode = null;
let analyser = null;
let vcoGain = null;
let building = null;
let timeData = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  shNode = await loadFaustNode(audioContext, "sh", "./generated");

  // white noise at +-4 V feeding S/H IN (used when testmode = 0)
  const len = 2 * audioContext.sampleRate;
  const buf = audioContext.createBuffer(1, len, audioContext.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = 8 * (Math.random() - 0.5);
  const noise = new AudioBufferSourceNode(audioContext, { buffer: buf, loop: true });
  noise.connect(shNode);
  noise.start();

  // held voltage is observed, not heard
  analyser = new AnalyserNode(audioContext, { fftSize: 512 });
  shNode.connect(analyser);
  const pull = new GainNode(audioContext, { gain: 0 });   // analyser must reach the destination to render
  analyser.connect(pull);
  pull.connect(audioContext.destination);

  // the patched VCO: pitch follows the held voltage (set per frame)
  const vco = new OscillatorNode(audioContext, { type: "sawtooth", frequency: 220 });
  vcoGain = new GainNode(audioContext, { gain: 0.25 * state.level });
  vco.connect(vcoGain);
  vcoGain.connect(audioContext.destination);
  vco.start();
  vcoRef = vco;

  pushAllParams();
}

let vcoRef = null;

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

const state = { clock: 0.5, droop: 0, source: "noise", level: 0.6 };

function setParam(addr, v) { if (shNode) shNode.setParamValue(addr, v); }

// DROOP: service exaggeration of the CA3140 bias droop. 0 = the real
// ~0.1 mV/s; full = 5 V/s, an audible downward glide between samples.
const droopVs = (k) => 1e-4 * (5e4 ** k);

function pushAllParams() {
  setParam(PARAM.clock, state.clock);
  setParam(PARAM.droop, droopVs(state.droop));
  setParam(PARAM.testmode, TESTMODE[state.source]);
  setParam(PARAM.sineHz, 0.11);   // slow MG-style sweep
  setParam(PARAM.rampSlope, 2.0); // V/s, wraps at the rails per the model
}

makeKnob($("knob-clock"), state.clock, (v) => { state.clock = v; setParam(PARAM.clock, v); });
makeKnob($("knob-droop"), state.droop, (v) => { state.droop = v; setParam(PARAM.droop, droopVs(v)); });
makeKnob($("knob-level"), state.level, (v) => {
  state.level = v;
  if (vcoGain) vcoGain.gain.setTargetAtTime(0.25 * v, audioContext.currentTime, 0.02);
});

makeChipGroup(document.querySelector(".chips"), (v) => {
  state.source = v;
  setParam(PARAM.testmode, TESTMODE[v]);
});

const power = makeRocker($("power"), false, powerToggle);

// clicking anywhere on the panel before power-up also starts it
document.querySelector(".panel").addEventListener("pointerdown", () => {
  if (!audioContext) powerOn();
}, { once: true });

// ---------- scope, lamp, VCO tracking ----------

function heldVoltage() {
  if (!analyser || audioContext.state !== "running") return null;
  if (!timeData) timeData = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(timeData);
  return timeData[timeData.length - 1];
}

const lampEl = $("shind");
let lampGlow = 0, lastHeld = null;

stripScope($("scope"), heldVoltage, {
  min: -6, max: 6, seconds: 8,
  onFrame(dt) {
    const v = heldVoltage();

    // S/H IND: C8/R27 flash stretcher - fires on each new sample
    if (v !== null && lastHeld !== null && Math.abs(v - lastHeld) > 0.05) lampGlow = 1;
    if (v !== null) lastHeld = v;
    lampGlow *= Math.exp(-dt / 0.12);
    const g = lampGlow;
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;

    // patch cord: held voltage -> VCO pitch (1 octave per 2 V around 220 Hz)
    if (v !== null && vcoRef) {
      const f = Math.min(2000, Math.max(40, 220 * 2 ** (v / 2)));
      vcoRef.frequency.setTargetAtTime(f, audioContext.currentTime, 0.005);
    }
  },
});
