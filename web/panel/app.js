// PS-3100 full panel: the composed 48-channel instrument wasm plus the
// modulation boards as live wasm nodes, patched onto the instrument's buses
// at control rate - the browser stands in for the panel pin-jacks:
//   MG1 tri -> VP1 (real attenuverter wasm) -> dest
//   MG2 tri (from the KLM-63 MOD-VCA board) -> depth -> dest
//   S&H (self-clocked noise steps) -> VP2 -> dest
// Destinations: resonator sweep bus, temperament (pitch) bus, filter bus.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, makeChipGroup, buildKeybed } from "../lib/panel.js";
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
  gegDelay: `${P}vca/geg/delay`,
  gegAttack: `${P}vca/geg/attack`,
  gegRelease: `${P}vca/geg/release`,
  cv2: `${P}vca/cv2`,
  rescv: `${P}resonator/cv`,
  peak1: `${P}resonator/peak1`,
  peak2: `${P}resonator/peak2`,
  peak3: `${P}resonator/peak3`,
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
let inst = null;
let mods = null;      // { mg1, mg2, sh, vp } nodes + analysers
let analyser = null;
let building = null;

let cvSink = null;   // zero-gain pull: analyser subgraphs must reach the
                     // destination or Chrome never renders them
function tapCv(node, channel = null) {
  if (!cvSink) {
    cvSink = new GainNode(audioContext, { gain: 0 });
    cvSink.connect(audioContext.destination);
  }
  const an = new AnalyserNode(audioContext, { fftSize: 512 });
  an.connect(cvSink);
  if (channel !== null) {         // pick ONE channel; plain connect down-mixes
    const split = new ChannelSplitterNode(audioContext, { numberOfOutputs: 2 });
    node.connect(split);
    split.connect(an, channel);
  } else {
    node.connect(an);
  }
  const buf = new Float32Array(an.fftSize);
  return () => { an.getFloatTimeDomainData(buf); return buf[buf.length - 1]; };
}

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });

  const [instrument, mg1, mg2, sh, vp] = await Promise.all([
    loadFaustNode(audioContext, "instrument_poly", "../poly/generated"),
    loadFaustNode(audioContext, "mg1_noise", "../mg1noise/generated"),
    loadFaustNode(audioContext, "modvca", "../modvca/generated"),
    loadFaustNode(audioContext, "sh", "../sh/generated"),
    loadFaustNode(audioContext, "vp", "../vp/generated"),
  ]);
  inst = instrument;

  // audio path: only the instrument reaches the speakers
  analyser = new AnalyserNode(audioContext, { fftSize: 4096, smoothingTimeConstant: 0.75 });
  const master = new GainNode(audioContext, { gain: 0.1 });
  const limiter = new DynamicsCompressorNode(audioContext,
    { threshold: -6, knee: 3, ratio: 12, attack: 0.002, release: 0.15 });
  inst.connect(analyser);
  analyser.connect(master);
  master.connect(limiter);
  limiter.connect(audioContext.destination);

  // worklets with unconnected inputs are treated as inactive and render
  // silence; a running silent source on the input keeps them processing
  const keepAlive = new ConstantSourceNode(audioContext, { offset: 0 });
  keepAlive.start();
  keepAlive.connect(mg1);
  keepAlive.connect(mg2);
  keepAlive.connect(vp);   // one input, channels are internal to the worklet

  // modulation boards: CV-rate, observed not heard
  mg1.setParamValue("/mg1_noise/outsel", 0);          // triangle pin 34
  mg2.setParamValue("/modvca/probe", 3);              // MG2 triangle on ch0
  sh.setParamValue("/sh/testmode", 0);                // external in = noise
  const noise = (() => {                               // noise -> S&H input
    const len = 2 * audioContext.sampleRate;
    const b = audioContext.createBuffer(1, len, audioContext.sampleRate);
    const d = b.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = 8 * (Math.random() - 0.5);
    const src = new AudioBufferSourceNode(audioContext, { buffer: b, loop: true });
    src.connect(sh);
    src.start();
    return src;
  })();

  watchAudioHealth(audioContext, [inst, mg1, mg2, sh, vp]);

  mods = {
    mg1, mg2, sh, vp,
    mg1Val: tapCv(mg1),
    mg2Val: tapCv(mg2),
    shVal: tapCv(sh),
    vp1Val: tapCv(vp, 0),   // out1 (monitor=0 -> y1)
    vp2Val: tapCv(vp, 1),   // out2 = y2
  };
  vp.setParamValue("/vp/monitor", 0);

  pushAllParams();
}

async function powerOn() {
  if (audioDead || audioContext?.state === "closed") {   // rebuild path
    try { await audioContext?.close(); } catch {}
    audioContext = null; inst = null; mods = null; analyser = null; building = null;
    cvSink = null;
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
  // full teardown: a fresh context on next power-on recovers from renderer
  // death and re-acquires the current output device
  try { await audioContext.close(); } catch {}
  audioContext = null; inst = null; mods = null; analyser = null; building = null;
  cvSink = null;   // belongs to the closed context
  document.getElementById("audio-badge")?.remove();
  power.set(false);
  $("power-lamp").classList.remove("on");
}

// ---------- panel state ----------

const state = {
  wfd: 0, tune: 0.5, vfc: 0.7, attack: 0.25, release: 0.35,
  gegDelay: 0, gegAttack: 0.1, gegRelease: 0.35, multiple: false,
  peak1: 0.5, peak2: 0.5, peak3: 0.5, blend: 0.7, cv2: 1, ensOn: true,
  mg1Rate: 0.5, vp1: 0.85, mg1Dest: "off",
  // every patch destination starts OFF: the panel powers up unmodulated, so a
  // plain note is a plain note until something is patched onto a bus
  mg2Rate: 0.5, mg2Depth: 0.85, mg2Dest: "off",   // depth/VP knobs are bipolar: 0.5 = null
  shClock: 0.5, vp2: 0.85, shDest: "off",
};

const setI = (addr, v) => { if (inst) inst.setParamValue(addr, v); };

const attackSec = (k) => 0.001 * (1000 ** k);
const releaseSec = (k) => 0.05 * (200 ** k);
const tuneVolts = (k) => -1.62 + (k - 0.5) * 4 * (0.93 / 12);

function pushAllParams() {
  setI(PARAM.wfd, state.wfd * 13);
  setI(PARAM.vfc, -14 + 14 * state.vfc);
  setI(PARAM.attack, attackSec(state.attack));
  setI(PARAM.release, releaseSec(state.release));
  setI(PARAM.cvTune, tuneVolts(state.tune));
  setI(PARAM.multiple, state.multiple ? 1 : 0);
  setI(PARAM.gegDelay, state.gegDelay);
  setI(PARAM.gegAttack, state.gegAttack);
  setI(PARAM.gegRelease, 1 - state.gegRelease);   // traced sense: krel=1 fast
  setI(PARAM.cv2, state.cv2);
  setI(PARAM.rescv, 0.5);   // base; modulation adds per frame
  setI(PARAM.peak1, state.peak1);
  setI(PARAM.peak2, state.peak2);
  setI(PARAM.peak3, state.peak3);
  setI(PARAM.blend, state.blend);
  setI(PARAM.bypass, state.ensOn ? 0 : 1);
  if (mods) {
    mods.mg1.setParamValue("/mg1_noise/vfc1", (state.mg1Rate - 0.5) * 6);
    mods.mg2.setParamValue("/modvca/mg2_rate", state.mg2Rate);
    mods.sh.setParamValue("/sh/clock", state.shClock);
    mods.vp.setParamValue("/vp/knob1", state.vp1);
    mods.vp.setParamValue("/vp/knob2", state.vp2);
  }
}

// instrument knobs
makeKnob($("knob-wfd"), state.wfd, (v) => { state.wfd = v; setI(PARAM.wfd, v * 13); });
makeKnob($("knob-tune"), state.tune, (v) => { state.tune = v; });
makeKnob($("knob-vfc"), state.vfc, (v) => { state.vfc = v; });
makeKnob($("knob-attack"), state.attack, (v) => { state.attack = v; setI(PARAM.attack, attackSec(v)); });
makeKnob($("knob-release"), state.release, (v) => { state.release = v; setI(PARAM.release, releaseSec(v)); });
makeKnob($("knob-gdel"), state.gegDelay, (v) => { state.gegDelay = v; setI(PARAM.gegDelay, v); });
makeKnob($("knob-gatt"), state.gegAttack, (v) => { state.gegAttack = v; setI(PARAM.gegAttack, v); });
makeKnob($("knob-grel"), state.gegRelease, (v) => { state.gegRelease = v; setI(PARAM.gegRelease, 1 - v); });
makeKnob($("knob-peak1"), state.peak1, (v) => { state.peak1 = v; setI(PARAM.peak1, v); });
makeKnob($("knob-peak2"), state.peak2, (v) => { state.peak2 = v; setI(PARAM.peak2, v); });
makeKnob($("knob-peak3"), state.peak3, (v) => { state.peak3 = v; setI(PARAM.peak3, v); });
makeKnob($("knob-blend"), state.blend, (v) => { state.blend = v; setI(PARAM.blend, v); });
makeKnob($("knob-cv2"), state.cv2, (v) => { state.cv2 = v; setI(PARAM.cv2, v); });

// modulation knobs
makeKnob($("knob-mg1rate"), state.mg1Rate, (v) => {
  state.mg1Rate = v;
  mods?.mg1.setParamValue("/mg1_noise/vfc1", (v - 0.5) * 6);
});
makeKnob($("knob-vp1"), state.vp1, (v) => { state.vp1 = v; mods?.vp.setParamValue("/vp/knob1", v); });
makeKnob($("knob-mg2rate"), state.mg2Rate, (v) => {
  state.mg2Rate = v;
  mods?.mg2.setParamValue("/modvca/mg2_rate", v);
});
makeKnob($("knob-mg2depth"), state.mg2Depth, (v) => { state.mg2Depth = v; });
makeKnob($("knob-shclock"), state.shClock, (v) => {
  state.shClock = v;
  mods?.sh.setParamValue("/sh/clock", v);
});
makeKnob($("knob-vp2"), state.vp2, (v) => { state.vp2 = v; mods?.vp.setParamValue("/vp/knob2", v); });

makeRocker($("trig-mode"), false, (on) => { state.multiple = on; setI(PARAM.multiple, on ? 1 : 0); });
makeRocker($("ens-on"), true, (on) => { state.ensOn = on; setI(PARAM.bypass, on ? 0 : 1); });
makeChipGroup($("mg1-dest"), (v) => { state.mg1Dest = v; });
makeChipGroup($("mg2-dest"), (v) => { state.mg2Dest = v; });
makeChipGroup($("sh-dest"), (v) => { state.shDest = v; });

const power = makeRocker($("power"), false, powerToggle);

// ---------- keybed -> bitmask ----------

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
  setI(PARAM.keysLo, lo >>> 0);
  setI(PARAM.keysHi, hi >>> 0);
  setI(PARAM.nkeys, held.size);
}

function noteOn(midi) {
  if (!audioContext || audioContext.state !== "running") { powerOn().then(() => noteOn(midi)); return; }
  held.add(midi);
  pushKeys();
}
function noteOff(midi) { held.delete(midi); pushKeys(); }

buildKeybed($("keybed"), { lo: KEY_LO, hi: KEY_HI, keymap: KEYMAP, onNoteOn: noteOn, onNoteOff: noteOff });

// ---------- the patch field: CVs -> instrument buses, per frame ----------

const lamp = (el, g) => {
  el.style.background = `rgb(${122 + 133 * g}, ${74 + 108 * g}, ${16 + 56 * g})`;
  el.style.boxShadow = `0 0 0 1px #000, 0 0 ${10 * g}px ${2 * g}px rgba(255,182,72,${0.75 * g})`;
};
const lamps = { mg1: $("mg1-lamp"), mg2: $("mg2-lamp"), sh: $("sh-lamp") };

window.__cvDebug = () => {
  let outRms = null;
  if (analyser) {
    const b = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(b);
    outRms = Math.sqrt(b.reduce((s2, v) => s2 + v * v, 0) / b.length);
  }
  return mods && {
    mg1: mods.mg1Val(), mg2: mods.mg2Val(), sh: mods.shVal(),
    vp1: mods.vp1Val(), vp2: mods.vp2Val(),
    outRms, ctxState: audioContext?.state, nkeys: held.size,
  };
};

spectrumScope($("scope"), () => analyser, () => audioContext.sampleRate, {
  onFrame() {
    if (!mods || !audioContext || audioContext.state !== "running") return;

    // MG1 tri (+-3.3 V) and S&H (+-~5 V) run through the real VP wasm
    mods.vp.setParamValue("/vp/vin1", mods.mg1Val() ?? 0);
    mods.vp.setParamValue("/vp/vin2", mods.shVal() ?? 0);
    const src = {
      mg1: (mods.vp1Val() ?? 0) / 3.5,          // VP1-processed MG1, ~unit
      mg2: ((mods.mg2Val() ?? 0) / 2.73) * (state.mg2Depth * 2 - 1), // bipolar depth
      sh: (mods.vp2Val() ?? 0) / 5.5,           // VP2-processed S&H
    };
    lamp(lamps.mg1, Math.min(1, Math.abs(src.mg1)));
    lamp(lamps.mg2, Math.min(1, Math.abs(src.mg2)));
    lamp(lamps.sh, Math.min(1, Math.abs(src.sh)));

    const sums = { sweep: 0, pitch: 0, cutoff: 0 };
    for (const [name, dest] of [["mg1", state.mg1Dest], ["mg2", state.mg2Dest], ["sh", state.shDest]]) {
      if (dest !== "off") sums[dest] += src[name];
    }
    setI(PARAM.rescv, Math.min(1, Math.max(0, 0.5 + 0.4 * sums.sweep)));
    setI(PARAM.cvTune, tuneVolts(state.tune) + 0.25 * sums.pitch);
    setI(PARAM.vfc, Math.min(0, Math.max(-14, -14 + 14 * state.vfc + 4 * sums.cutoff)));
  },
});
