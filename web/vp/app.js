// KLM-76 voltage processors bench. CV-rate module: the test CVs (slow sine
// into VP1, DC into VP2) are driven through the vin sliders at frame rate,
// and the selected output is drawn on the strip-chart. No audio reaches the
// speakers - this board processes control voltages.
import { loadFaustNode } from "../lib/faust-loader.js";
import { makeKnob, makeRocker, makeChipGroup } from "../lib/panel.js";
import { stripScope } from "../lib/scope.js";

const PARAM = {
  k1: "/vp/knob1", k2: "/vp/knob2",
  vin1: "/vp/vin1", vin2: "/vp/vin2",
  monitor: "/vp/monitor",
};

const $ = (id) => document.getElementById(id);

let audioContext = null;
let node = null;
let analyser = null;
let building = null;
let timeData = null;

async function buildAudio() {
  audioContext = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  node = await loadFaustNode(audioContext, "vp", "./generated");

  // worklets with unconnected inputs are treated as inactive and render
  // silence; a running silent source on the input keeps them processing
  const keepAlive = new ConstantSourceNode(audioContext, { offset: 0 });
  keepAlive.start();
  keepAlive.connect(node);   // one input, channels are internal to the worklet

  // observe out1 (monitor selects which channel it carries); nothing audible
  analyser = new AnalyserNode(audioContext, { fftSize: 512 });
  const splitter = new ChannelSplitterNode(audioContext, { numberOfOutputs: 2 });
  node.connect(splitter);
  splitter.connect(analyser, 0);
  const pull = new GainNode(audioContext, { gain: 0 });   // analyser must reach the destination to render
  analyser.connect(pull);
  pull.connect(audioContext.destination);

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

const state = { k1: 1.0, k2: 0.5, rate: 0.4, dc: 0.5 };
let sinePhase = 0;

function setParam(addr, v) { if (node) node.setParamValue(addr, v); }

function pushAllParams() {
  setParam(PARAM.k1, state.k1);
  setParam(PARAM.k2, state.k2);
  setParam(PARAM.vin2, (state.dc - 0.5) * 20);
  setParam(PARAM.monitor, 0);
}

makeKnob($("knob-k1"), state.k1, (v) => { state.k1 = v; setParam(PARAM.k1, v); });
makeKnob($("knob-k2"), state.k2, (v) => { state.k2 = v; setParam(PARAM.k2, v); });
makeKnob($("knob-rate"), state.rate, (v) => { state.rate = v; });
makeKnob($("knob-dc"), state.dc, (v) => { state.dc = v; setParam(PARAM.vin2, (v - 0.5) * 20); });

makeChipGroup(document.querySelector(".chips"), (v) => setParam(PARAM.monitor, Number(v)));

const power = makeRocker($("power"), false, powerToggle);
document.querySelector(".panel").addEventListener("pointerdown", () => {
  if (!audioContext) powerOn();
}, { once: true });

// ---------- scope ----------

function outVolts() {
  if (!analyser || audioContext.state !== "running") return null;
  if (!timeData) timeData = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(timeData);
  return timeData[timeData.length - 1];
}

stripScope($("scope"), outVolts, {
  min: -8, max: 8, seconds: 8,
  onFrame(dt) {
    if (audioContext && audioContext.state === "running") {
      sinePhase += 2 * Math.PI * (0.05 * 20 ** state.rate) * dt; // 0.05..1 Hz
      setParam(PARAM.vin1, 5 * Math.sin(sinePhase));
    }
  },
});
