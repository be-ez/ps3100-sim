// KLM-76 VCA bench. A mono saw voice runs continuously into the wasm VCA
// chain; the keybed gates VCA 1's CV (the GEG OUT patch in the real
// instrument), so the attack/release you hear is the vactrol dynamics.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, makeChipGroup, buildKeybed } from "../lib/panel.js";
import { stripScope } from "../lib/scope.js";

const PARAM = { cv1: "/vca/cv1", cv2: "/vca/cv2", monitor: "/vca/monitor" };

const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);

// ---------- audio graph ----------

let audioContext = null;
let vcaNode = null;
let analyser = null;
let osc = null;
let building = null;
let timeData = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  vcaNode = await loadFaustNode(audioContext, "vca", "./generated");

  // mono saw drone at +-4 "volts", always running; the VCA does the gating
  osc = new OscillatorNode(audioContext, { type: "sawtooth", frequency: 110 });
  const oscLevel = new GainNode(audioContext, { gain: 4 });
  osc.connect(oscLevel);
  oscLevel.connect(vcaNode);
  osc.start();

  analyser = new AnalyserNode(audioContext, { fftSize: 2048 });
  const master = new GainNode(audioContext, { gain: 0.08 });   // volts -> speaker
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  vcaNode.connect(analyser);
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

const state = { cv1: 0.85, cv2: 1.0, mgOn: false, mgRate: 0.5, mgDepth: 0.5, gate: false };

function setParam(addr, v) { if (vcaNode) vcaNode.setParamValue(addr, v); }

// keybed gate scales VCA1 CONT; MG wobbles VCA2 CONT
let mgPhase = 0;

const cv1Now = () => (state.gate ? state.cv1 : 0);
const cv2Now = () => {
  if (!state.mgOn || state.mgDepth === 0) return state.cv2;
  const wobble = 0.5 * state.mgDepth * (Math.sin(mgPhase) - 1); // dips below the knob
  return Math.min(1, Math.max(0, state.cv2 + wobble));
};

function pushAllParams() {
  setParam(PARAM.cv1, cv1Now());
  setParam(PARAM.cv2, cv2Now());
  setParam(PARAM.monitor, 0);
}

makeKnob($("knob-cv1"), state.cv1, (v) => { state.cv1 = v; setParam(PARAM.cv1, cv1Now()); });
makeKnob($("knob-cv2"), state.cv2, (v) => { state.cv2 = v; setParam(PARAM.cv2, cv2Now()); });
makeKnob($("knob-mgrate"), state.mgRate, (v) => { state.mgRate = v; });
makeKnob($("knob-mgdepth"), state.mgDepth, (v) => { state.mgDepth = v; });

makeRocker($("mg-on"), false, (on) => {
  state.mgOn = on;
  if (!on) setParam(PARAM.cv2, state.cv2);
});

makeChipGroup(document.querySelector(".chips"), (v) => setParam(PARAM.monitor, Number(v)));

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed: mono last-note priority, gates VCA1 ----------

const held = [];

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  if (!held.includes(midi)) held.push(midi);
  osc.frequency.setTargetAtTime(440 * 2 ** ((midi - 69) / 12), audioContext.currentTime, 0.004);
  state.gate = true;
  setParam(PARAM.cv1, cv1Now());
}

function noteOff(midi) {
  const i = held.indexOf(midi);
  if (i >= 0) held.splice(i, 1);
  if (held.length) {
    osc?.frequency.setTargetAtTime(
      440 * 2 ** ((held[held.length - 1] - 69) / 12), audioContext.currentTime, 0.004);
  } else {
    state.gate = false;
    setParam(PARAM.cv1, 0);
  }
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope (output envelope) + vactrol lamps ----------

function outputPeak() {
  if (!analyser || audioContext.state !== "running") return null;
  if (!timeData) timeData = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(timeData);
  let peak = 0;
  for (let i = 0; i < timeData.length; i++) peak = Math.max(peak, Math.abs(timeData[i]));
  return peak;
}

const lampEls = [$("vac1"), $("vac2")];
const lampGlow = [0, 0];

stripScope($("scope"), outputPeak, {
  min: 0, max: 6, seconds: 8,
  onFrame(dt) {
    if (state.mgOn && audioContext && audioContext.state === "running") {
      mgPhase += 2 * Math.PI * (0.3 * 30 ** state.mgRate) * dt; // 0.3..1.6..9 Hz
      setParam(PARAM.cv2, cv2Now());
    }
    // LED drive is instantaneous with CV; the lamp lag mirrors the LDR side
    const drives = (audioContext && audioContext.state === "running")
      ? [cv1Now(), cv2Now()] : [0, 0];
    lampEls.forEach((el, i) => {
      const tau = drives[i] > lampGlow[i] ? 0.02 : 0.25;
      lampGlow[i] += (drives[i] - lampGlow[i]) * Math.min(1, dt / tau);
      const g = lampGlow[i];
      el.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
      el.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
    });
  },
});
