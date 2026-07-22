// PS-3100 full keyboard: ONE wasm (dsp/instrument_poly.dsp). All 48 note
// channels (12 phase-locked divider chains x 4 octave rows, per-key CD4007
// envelopes + KORG35 cells) run inside the DSP with no voice allocation -
// the keybed just sets the key bitmask, exactly like the hardware's key
// contacts. Trigger conditioning (SINGLE/MULTIPLE) gates the shared GEG-VCA
// in-DSP; resonators and ensemble follow.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, buildKeybed } from "../lib/panel.js";
import { spectrumScope } from "../lib/scope.js";

const P = "/instrument_poly/";
const PARAM = {
  keysLo: `${P}poly/poly/keys_lo`,
  keysHi: `${P}poly/poly/keys_hi`,
  wfd: `${P}poly/poly/wfd`,
  vfc: `${P}poly/poly/vfc`,
  attack: `${P}poly/poly/attack`,
  release: `${P}poly/poly/release`,
  cvTune: `${P}poly/poly/cv`,
  nkeys: `${P}vca/geg/trigger/nkeys`,
  multiple: `${P}vca/geg/trigger/multiple`,
  gegAttack: `${P}vca/geg/attack`,
  gegRelease: `${P}vca/geg/release`,
  cv2: `${P}vca/cv2`,
  rescv: `${P}resonator/cv`,
  blend: `${P}resonator/blend`,
  bypass: `${P}ensemble/bypass`,
};

const KEY_LO = 41, KEY_HI = 76;
const KEYMAP = {
  a: 48, w: 49, s: 50, e: 51, d: 52, f: 53, t: 54, g: 55, y: 56, h: 57,
  u: 58, j: 59, k: 60, o: 61, l: 62, p: 63, ";": 64, "'": 65,
};

const $ = (id) => document.getElementById(id);


function audioBadge(text) {
  let el = document.getElementById("audio-badge");
  if (!el) {
    el = document.createElement("span");
    el.id = "audio-badge";
    el.className = "legend-sm";
    el.style.color = "#e04c3a";
    document.querySelector(".power-block")?.appendChild(el);
  }
  el.textContent = text;
}



// Chrome's audio renderer can die (device switch, e.g. bluetooth handoff, or
// renderer crash) - resume() never recovers such a context, only a fresh one
// does. But closing a HEALTHY context with many live worklets can itself
// crash the tab, so: normal power toggling suspends/resumes, and the full
// teardown+rebuild runs ONLY when the context is detected dead.
let audioDead = false;
function markDead(why) {
  if (audioDead) return;
  audioDead = true;
  console.warn("audio renderer lost:", why);
  audioBadge("AUDIO LOST \u2014 flip POWER off/on");
}
function watchAudioHealth(ctx, nodes) {
  ctx.onstatechange = () => {
    if (ctx.state === "interrupted") markDead("context " + ctx.state);
  };
  for (const n of nodes) {
    if (n && "onprocessorerror" in n) {
      n.onprocessorerror = (e) => { console.error(e); markDead("processor error"); };
    }
  }
}

function warnIfSpFallback() {
  if (typeof window === "undefined" || !window.__spFallback) return;
  const el = document.createElement("span");
  el.className = "legend-sm";
  el.style.color = "#e04c3a";
  el.textContent = "SP MODE \u2014 use https/localhost";
  document.querySelector(".power-block")?.appendChild(el);
}


let audioContext = null;
let node = null;
let analyser = null;
let building = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "instrument_poly", "./generated");

  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const master = new GainNode(audioContext, { gain: 0.1 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });

  watchAudioHealth(audioContext, [node]);
  node.connect(analyser);
  analyser.connect(master);
  master.connect(limiter);
  limiter.connect(audioContext.destination);

  pushAllParams();
}

async function powerOn() {
  if (audioDead || audioContext?.state === "closed") {   // rebuild path
    try { await audioContext?.close(); } catch {}
    audioContext = null; node = null; analyser = null; building = null;
    audioDead = false;
    document.getElementById("audio-badge")?.remove();
  }
  if (!building) building = buildAudio();
  await building;
  if (audioContext.state !== "running") {
    await audioContext.resume();
    // a resume that never reaches "running" means the renderer is gone
    await new Promise((ok) => setTimeout(ok, 300));
    if (audioContext.state !== "running") { markDead("resume stalled"); return; }
  }
  power.set(true);
  $("power-lamp").classList.add("on");
  warnIfSpFallback();
}

async function powerToggle(wantOn) {
  if (wantOn || !audioContext || audioContext.state !== "running") return powerOn();
  await audioContext.suspend();
  power.set(false);
  $("power-lamp").classList.remove("on");
}

// ---------- panel state ----------

const state = {
  wfd: 0, vfc: 0.7, attack: 0.25, release: 0.35, tune: 0.5,
  multiple: false, gegAttack: 0.1, gegRelease: 0.35,
  cv: 0.5, blend: 0.7, ensOn: true,
};

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

const attackSec = (k) => 0.001 * (1000 ** k);           // 1 ms .. 1 s
const releaseSec = (k) => 0.05 * (200 ** k);            // 50 ms .. 10 s
// temperament bus: 0.93 V/oct; TUNE spans +-2 semitones around neutral
const tuneVolts = (k) => -1.62 + (k - 0.5) * 4 * (0.93 / 12);

function pushAllParams() {
  setParam(PARAM.wfd, state.wfd * 13);
  setParam(PARAM.vfc, -14 + 14 * state.vfc);
  setParam(PARAM.attack, attackSec(state.attack));
  setParam(PARAM.release, releaseSec(state.release));
  setParam(PARAM.cvTune, tuneVolts(state.tune));
  setParam(PARAM.multiple, state.multiple ? 1 : 0);
  setParam(PARAM.gegAttack, state.gegAttack);
  setParam(PARAM.gegRelease, 1 - state.gegRelease);  // traced sense: krel=1 fast
  setParam(PARAM.cv2, 1);
  setParam(PARAM.rescv, state.cv);
  setParam(PARAM.blend, state.blend);
  setParam(PARAM.bypass, state.ensOn ? 0 : 1);
}

makeKnob($("knob-wfd"), state.wfd, (v) => { state.wfd = v; setParam(PARAM.wfd, v * 13); });
makeKnob($("knob-vfc"), state.vfc, (v) => { state.vfc = v; setParam(PARAM.vfc, -14 + 14 * v); });
makeKnob($("knob-attack"), state.attack, (v) => { state.attack = v; setParam(PARAM.attack, attackSec(v)); });
makeKnob($("knob-release"), state.release, (v) => { state.release = v; setParam(PARAM.release, releaseSec(v)); });
makeKnob($("knob-tune"), state.tune, (v) => { state.tune = v; setParam(PARAM.cvTune, tuneVolts(v)); });
makeKnob($("knob-gatt"), state.gegAttack, (v) => { state.gegAttack = v; setParam(PARAM.gegAttack, v); });
makeKnob($("knob-grel"), state.gegRelease, (v) => { state.gegRelease = v; setParam(PARAM.gegRelease, 1 - v); });
makeKnob($("knob-cv"), state.cv, (v) => { state.cv = v; setParam(PARAM.rescv, v); });
makeKnob($("knob-blend"), state.blend, (v) => { state.blend = v; setParam(PARAM.blend, v); });

makeRocker($("trig-mode"), false, (on) => {
  state.multiple = on;
  setParam(PARAM.multiple, on ? 1 : 0);
});
makeRocker($("ens-on"), true, (on) => {
  state.ensOn = on;
  setParam(PARAM.bypass, on ? 0 : 1);
});

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed -> key bitmask (bit = pc*4 + octRow) ----------

const held = new Set();

function pushKeys() {
  let lo = 0, hi = 0;
  for (const midi of held) {
    const pc = (midi - 41) % 12;
    const oct = 3 - Math.floor((midi - 41) / 12);
    const bit = pc * 4 + oct;
    if (bit < 24) lo |= 1 << bit;
    else hi |= 1 << (bit - 24);
  }
  setParam(PARAM.keysLo, lo >>> 0);
  setParam(PARAM.keysHi, hi >>> 0);
  setParam(PARAM.nkeys, held.size);
}

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  held.add(midi);
  pushKeys();
}

function noteOff(midi) {
  held.delete(midi);
  pushKeys();
}

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- scope + trigger lamp ----------

const lampEl = $("trig-lamp");
let lampGlow = 0;

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  onFrame(dt) {
    // lamp mirrors the conditioned trigger: SINGLE holds, MULTIPLE pulses
    const on = held.size > 0 && audioContext && audioContext.state === "running";
    const target = state.multiple ? 0 : (on ? 1 : 0);
    if (state.multiple && on && lampGlow < 0.05) lampGlow = 1;  // pulse per attack batch
    const tau = target > lampGlow ? 0.02 : 0.12;
    lampGlow += ((state.multiple ? 0 : target) - lampGlow) * Math.min(1, dt / tau);
    const g = Math.max(0, Math.min(1, lampGlow));
    lampEl.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
    lampEl.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
  },
});
