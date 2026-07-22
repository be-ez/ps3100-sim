// KLM-62 resonator bench. The Faust DSP (dsp/resonator.dsp) is the wet path;
// a polyphonic sawtooth keybed stands in for the synth voices that feed the
// resonator board in the real instrument.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, makeChipGroup, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const PARAM = {
  cv: "/resonator/cv",
  blend: "/resonator/blend",
  color: "/resonator/color",
  peak1: "/resonator/peak1",
  peak2: "/resonator/peak2",
  peak3: "/resonator/peak3",
};

// keybed range: F2..E5 like the real 48-key F-to-E bed, one octave shorter
const KEY_LO = 41, KEY_HI = 76;

// computer keyboard, chromatic from C3 (two piano-style rows)
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

// ---------- audio graph (built lazily on first user gesture) ----------

let audioContext = null;
let faustNode = null;
let analyser = null;
let voiceBus = null;
let building = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  faustNode = await loadFaustNode(audioContext, "resonator", "./generated");

  voiceBus = new GainNode(audioContext, { gain: 0.1 });
  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  voiceBus.connect(faustNode);
  faustNode.connect(analyser);
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

const state = { cv: 0.5, blend: 0.85, color: 0, mgOn: false, mgRate: 0.35, mgDepth: 0.4, peak1: 0.5, peak2: 0.5, peak3: 0.5 };

function setParam(addr, v) { if (faustNode) faustNode.setParamValue(addr, v); }

function pushAllParams() {
  setParam(PARAM.cv, currentCv());
  setParam(PARAM.blend, state.blend);
  setParam(PARAM.color, state.color);
  setParam(PARAM.peak1, state.peak1);
  setParam(PARAM.peak2, state.peak2);
  setParam(PARAM.peak3, state.peak3);
}

// MG: slow sine on the resonator CV, like patching MG1 to the resonator
// frequency input on the real panel. The vactrol model smooths the 60 Hz
// control updates.
let mgPhase = 0, mgLast = 0;

function currentCv() {
  if (!state.mgOn || state.mgDepth === 0) return state.cv;
  const wobble = 0.5 * state.mgDepth * Math.sin(mgPhase);
  return Math.min(1, Math.max(0, state.cv + wobble));
}

makeKnob($("knob-cv"), state.cv, (v) => { state.cv = v; setParam(PARAM.cv, currentCv()); });
makeKnob($("knob-blend"), state.blend, (v) => { state.blend = v; setParam(PARAM.blend, v); });
makeKnob($("knob-peak1"), state.peak1, (v) => { state.peak1 = v; setParam(PARAM.peak1, v); });
makeKnob($("knob-peak2"), state.peak2, (v) => { state.peak2 = v; setParam(PARAM.peak2, v); });
makeKnob($("knob-peak3"), state.peak3, (v) => { state.peak3 = v; setParam(PARAM.peak3, v); });
makeKnob($("knob-mgrate"), state.mgRate, (v) => { state.mgRate = v; });
makeKnob($("knob-mgdepth"), state.mgDepth, (v) => { state.mgDepth = v; });

makeRocker($("mg-on"), false, (on) => {
  state.mgOn = on;
  if (!on) setParam(PARAM.cv, state.cv);
});

makeChipGroup(document.querySelector(".chips"), (v) => {
  state.color = Number(v);
  setParam(PARAM.color, state.color);
});

const power = makeRocker($("power"), false, powerToggle);

// ---------- voices ----------

const voices = new Map(); // midi -> {osc, env}

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

// ---------- scope + vactrol lamps ----------

const lampEls = [$("vac1"), $("vac2"), $("vac3")];
// stage LED drives track the octave stagger (R, R/2, R/4): higher band, harder drive
const LAMP_SCALE = [0.55, 0.75, 1.0];
const lampGlow = [0, 0, 0];

// SPICE reference curves (ngspice AC, yellow variant, per base-Rldr grid).
// The current CV maps to a static Rldr via the reconciled bipolar sweep-bus
// law (dsp/resonator.dsp): vbus = 10*(cv-0.5), Rldr = 47k * 2^(-0.425*vbus);
// the overlay log-interpolates between grid curves.
let spice = null;
fetch("./spice-curves.json").then((r) => r.json()).then((d) => { spice = d; });

const atFactory = () =>
  Math.abs(state.peak1 - 0.5) < 0.02 && Math.abs(state.peak2 - 0.5) < 0.02
  && Math.abs(state.peak3 - 0.5) < 0.02;

function spiceOverlay() {
  // curves are yellow-only and assume the factory peak stagger
  if (!spice || state.color !== 0 || !atFactory()) return null;
  const vbus = 10 * (currentCv() - 0.5);
  const r = Math.min(1e6, Math.max(4700, 47000 * 2 ** (-0.425 * vbus)));
  const grid = spice.rldrs;
  let i = 0;
  while (i < grid.length - 2 && grid[i + 1] < r) i++;
  const t = Math.log(r / grid[i]) / Math.log(grid[i + 1] / grid[i]);
  const a = spice.curves[`${grid[i]}`], b = spice.curves[`${grid[i + 1]}`];
  const db = a.map((v, n) => v + (b[n] - v) * t);
  return { freq: spice.freq, db, label: `SPICE REF · Rldr ${(r / 1000).toFixed(1)}k` };
}

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  overlay: spiceOverlay,
  onFrame(dt) {
    // MG runs inside the draw loop so the lamps and CV stay in sync
    if (state.mgOn && audioContext && audioContext.state === "running") {
      mgPhase += 2 * Math.PI * (0.05 * 8 ** state.mgRate) * dt; // 0.05..0.4..3.2 Hz
      const cv = currentCv();
      if (Math.abs(cv - mgLast) > 0.0005) { setParam(PARAM.cv, cv); mgLast = cv; }
    }
    // vactrol lamps: fast attack, slow dark decay, like the LDR itself
    const drive = (audioContext && audioContext.state === "running") ? currentCv() : 0;
    lampEls.forEach((el, i) => {
      const target = drive * LAMP_SCALE[i];
      const tau = target > lampGlow[i] ? 0.02 : 0.25;
      lampGlow[i] += (target - lampGlow[i]) * Math.min(1, dt / tau);
      const g = lampGlow[i];
      el.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
      el.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
    });
  },
});
