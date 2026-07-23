// Offline golden tests for the wasm board builds (no browser needed):
//   node web/test/node-selftest.mjs
// Renders each board's wasm through faustwasm's offline processor and checks
// it against the same references the pytest suite uses. Exit code != 0 on
// failure, so this can gate CI.
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const webDir = dirname(dirname(fileURLToPath(import.meta.url)));
const repoDir = dirname(webDir);
const { FaustMonoDspGenerator } = await import(join(webDir, "lib/faustwasm/index.js"));

const SR = 48000;
let failures = 0;

const check = (label, ok, detail) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${detail ? `  (${detail})` : ""}`);
  if (!ok) failures++;
};

async function makeProcessor(boardId) {
  const base = join(webDir, boardId, "generated");
  const dspMeta = JSON.parse(await readFile(join(base, "dsp-meta.json"), "utf8"));
  const dspModule = await WebAssembly.compile(await readFile(join(base, "dsp-module.wasm")));
  const gen = new FaustMonoDspGenerator();
  return gen.createOfflineProcessor(SR, 256,
    { module: dspModule, json: JSON.stringify(dspMeta), soundfiles: {} });
}

function goertzelDb(x, f) {
  const w = 2 * Math.PI * f / SR, c = 2 * Math.cos(w);
  let s1 = 0, s2 = 0;
  for (let n = 0; n < x.length; n++) { const s0 = x[n] + c * s1 - s2; s2 = s1; s1 = s0; }
  return 20 * Math.log10(Math.sqrt(s1 * s1 + s2 * s2 - c * s1 * s2) + 1e-30);
}

// ---- KLM-62 resonator: impulse peaks vs analysis/reference.json ----
{
  const RLDR = 100000, COLOR = 0, N = 1 << 17;
  const proc = await makeProcessor("resonator");
  proc.setParamValue("/resonator/bypass_vactrol", 1);
  proc.setParamValue("/resonator/rldr", RLDR);
  proc.setParamValue("/resonator/color", COLOR);
  proc.setParamValue("/resonator/blend", 1);

  const input = new Float32Array(N);
  input[0] = 1;
  const out = proc.render([input], N)[0];

  // Python's json.dump writes bare NaN/Infinity, which strict JSON.parse rejects
  const refText = (await readFile(join(repoDir, "analysis/reference.json"), "utf8"))
    .replace(/\bNaN\b/g, "null").replace(/\b-?Infinity\b/g, "null");
  const ref = JSON.parse(refText);
  const bands = ref.colors.yellow[String(RLDR)].bands;
  for (const band of bands) {
    // local peak search +-6% around the SPICE band center
    let best = -1e9, bestF = 0;
    for (let f = band.f0 * 0.94; f <= band.f0 * 1.06; f *= 1.004) {
      const db = goertzelDb(out, f);
      if (db > best) { best = db; bestF = f; }
    }
    const centsOff = Math.abs(1200 * Math.log2(bestF / band.f0));
    const dbOff = Math.abs(best - band.peak_db);
    check(`resonator band @ ${band.f0.toFixed(0)} Hz`,
      centsOff < 60 && dbOff < 2.0,
      `found ${bestF.toFixed(0)} Hz ${best.toFixed(1)} dB, ref ${band.peak_db.toFixed(1)} dB`);
  }
}

// ---- KLM-76 S&H: clock law at pot center + DC accuracy + step count ----
{
  const N = SR * 10; // 10 s
  const proc = await makeProcessor("sh");
  proc.setParamValue("/sh/clock", 0.5);       // panel: ~1.2 Hz at center
  proc.setParamValue("/sh/testmode", 3);      // DC level
  proc.setParamValue("/sh/dc_level", 2.0);
  const out = proc.render([new Float32Array(N)], N)[0];

  // settled output ~= gainBuf * DC = 1.1 * 2.0
  const settled = out[N - 1];
  check("sh DC accuracy", Math.abs(settled - 2.2) < 0.05, `settled ${settled.toFixed(3)} V`);

  // count sample events on a ramp: steps per 10 s ~= clock Hz * 10
  const proc2 = await makeProcessor("sh");
  proc2.setParamValue("/sh/clock", 0.5);
  proc2.setParamValue("/sh/testmode", 1);     // ramp
  proc2.setParamValue("/sh/ramp_slope", 1.0);
  const out2 = proc2.render([new Float32Array(N)], N)[0];
  // one acquisition spans several samples; use a 10 ms refractory window so
  // each sampling event counts once
  let steps = 0, lastStep = -SR;
  for (let n = 1; n < N; n++) {
    if (out2[n] - out2[n - 1] > 0.05 && n - lastStep > SR * 0.01) { steps++; lastStep = n; }
    else if (out2[n] - out2[n - 1] > 0.05) lastStep = n;
  }
  const hz = steps / 10;
  check("sh clock ~1.2 Hz at pot center", hz > 0.9 && hz < 1.5, `${hz.toFixed(2)} Hz`);
}

// ---- KLM-76 VCA: static-law gain at full and low CV (1 kHz sine) ----
{
  const N = SR; // 1 s
  const sine = new Float32Array(N);
  for (let n = 0; n < N; n++) sine[n] = Math.sin(2 * Math.PI * 1000 * n / SR);

  async function gainDbAt(cv) {
    const proc = await makeProcessor("vca");
    proc.setParamValue("/vca/bypass_vactrol", 1);
    proc.setParamValue("/vca/cv1", cv);
    proc.setParamValue("/vca/cv2", cv);
    const out = proc.render([sine], N)[0];
    // skip the DC-block settle, compare steady-state halves
    const tail = out.subarray(N / 2), ref = sine.subarray(N / 2);
    return goertzelDb(tail, 1000) - goertzelDb(ref, 1000);
  }

  const g1 = await gainDbAt(1.0);
  check("vca ~unity gain at full CV", Math.abs(g1) < 1.0, `${g1.toFixed(2)} dB`);
  const g0 = await gainDbAt(0.2);
  check("vca deep attenuation at low CV", g0 < -30, `${g0.toFixed(1)} dB`);
}

// ---- KLM-76 ensemble: bypass passthrough + BBD delay at frozen LFO ----
{
  const N = SR; // 1 s
  const sine = new Float32Array(N);
  for (let n = 0; n < N; n++) sine[n] = Math.sin(2 * Math.PI * 1000 * n / SR);

  const proc = await makeProcessor("ensemble");
  proc.setParamValue("/ensemble/bypass", 1);
  const out = proc.render([sine], N)[0];
  const g = goertzelDb(out.subarray(N / 2), 1000) - goertzelDb(sine.subarray(N / 2), 1000);
  check("ensemble bypass is transparent", Math.abs(g) < 0.01, `${g.toFixed(3)} dB`);

  // frozen LFO at phase 0 -> Vfm = vmid = +7.45 V (DC-direct single-supply
  // coupling, U6 re-read) -> tau = 256/(37209.9 + 1868.74*7.45) = 5.007 ms,
  // read back through the monitor hook (monitor 2 outputs tau1 in ms)
  const proc2 = await makeProcessor("ensemble");
  proc2.setParamValue("/ensemble/lfo_freeze", 1);
  proc2.setParamValue("/ensemble/lfo_phase_a", 0);
  proc2.setParamValue("/ensemble/monitor", 2);
  const tauOut = proc2.render([new Float32Array(4800)], 4800)[0];
  const tau = tauOut[4799];
  check("ensemble BBD tau at frozen LFO", Math.abs(tau - 5.007) < 0.01, `${tau.toFixed(3)} ms`);
}

// ---- KLM-63 MOD-VCA: static gain law + MG2 triangle amplitude ----
{
  const N = SR;
  const sine = new Float32Array(N);
  for (let n = 0; n < N; n++) sine[n] = Math.sin(2 * Math.PI * 1000 * n / SR);

  async function gainDbAt(rldr) {
    const proc = await makeProcessor("modvca");
    proc.setParamValue("/modvca/bypass_vactrol", 1);
    proc.setParamValue("/modvca/rldr", rldr);
    const out = proc.render([sine], N)[0];
    return goertzelDb(out.subarray(N / 2), 1000) - goertzelDb(sine.subarray(N / 2), 1000);
  }

  // g = 0.75 * VR201 / (Rldr + 2.2k): lit ~+2.8 dB, dark ~-42 dB
  const lit = await gainDbAt(3200);
  check("modvca lit gain ~+2.8 dB", Math.abs(lit - 2.8) < 1.0, `${lit.toFixed(2)} dB`);
  const dark = await gainDbAt(1e6);
  check("modvca dark attenuation", dark < -38, `${dark.toFixed(1)} dB`);

  // MG2 triangle via probe 3: amplitude +-2.73 V at pin 27
  const proc = await makeProcessor("modvca");
  proc.setParamValue("/modvca/probe", 3);
  proc.setParamValue("/modvca/mg2_rate", 0.5);
  const tri = proc.render([new Float32Array(SR * 4)], SR * 4)[0];
  let mn = 1e9, mx = -1e9;
  for (let n = SR; n < SR * 4; n++) { mn = Math.min(mn, tri[n]); mx = Math.max(mx, tri[n]); }
  check("mg2 triangle amplitude +-2.73 V",
    Math.abs(mx - 2.73) < 0.1 && Math.abs(mn + 2.73) < 0.1,
    `${mn.toFixed(2)}..${mx.toFixed(2)} V`);
}

// ---- KLM-63 MG1/noise: square pin level + pink filter slope ----
{
  // square (outsel 2): 0.18478 * (+/-13 V) = +-2.402 V
  const proc = await makeProcessor("mg1noise");
  proc.setParamValue("/mg1_noise/outsel", 2);
  const sq = proc.render([new Float32Array(SR * 2)], SR * 2)[0];
  let mn = 1e9, mx = -1e9;
  for (let n = SR / 2; n < SR * 2; n++) { mn = Math.min(mn, sq[n]); mx = Math.max(mx, sq[n]); }
  check("mg1 square pin +-2.40 V", Math.abs(mx - 2.402) < 0.05 && Math.abs(mn + 2.402) < 0.05,
    `${mn.toFixed(2)}..${mx.toFixed(2)} V`);

  // pink filter (outsel 6, test hook): ~ -10 dB/decade -> ~-20 dB from 100 Hz to 10 kHz
  async function pinkGainAt(f) {
    const p = await makeProcessor("mg1noise");
    p.setParamValue("/mg1_noise/outsel", 6);
    const N = SR;
    const s = new Float32Array(N);
    for (let n = 0; n < N; n++) s[n] = Math.sin(2 * Math.PI * f * n / SR);
    const out = p.render([s], N)[0];
    return goertzelDb(out.subarray(N / 2), f) - goertzelDb(s.subarray(N / 2), f);
  }
  // the real IC31b ladder is pink-ish only inside its shelf region: measured
  // (and SPICE-refereed in tests/test_mg1_noise_dsp.py) -8.2 dB for
  // 100 Hz -> 1 kHz, steeper above; anchor both points
  const g100 = await pinkGainAt(100), g1k = await pinkGainAt(1000);
  check("pink ladder mid-band slope", Math.abs((g1k - g100) + 8.2) < 2,
    `${(g1k - g100).toFixed(1)} dB/decade at 100 Hz..1 kHz`);
  check("pink ladder absolute gain at 1 kHz", Math.abs(g1k - 12.7) < 1, `${g1k.toFixed(1)} dB`);
}

// ---- KLM-62D balance/AM: sidebands + the documented ring null ----
// intensity->0 is explicitly outside the model's validity (the JFET leaves
// its resistive region, per the dsp header), so the second anchor is the
// VR302 ring null: at bias=0.4110 the carrier is suppressed and only
// sidebands remain (ring-mod behavior)
{
  const N = SR;
  async function sidebandDb(bias) {
    const proc = await makeProcessor("balance");
    proc.setParamValue("/balance_am/ttones", 1);
    proc.setParamValue("/balance_am/fcar", 2000);
    proc.setParamValue("/balance_am/acar", 1.0);
    proc.setParamValue("/balance_am/fmod_hz", 200);
    proc.setParamValue("/balance_am/amod", 2.0);
    proc.setParamValue("/balance_am/intensity", 1.0);
    proc.setParamValue("/balance_am/bias", bias);
    const out = proc.render([new Float32Array(N)], N)[0];
    const tail = out.subarray(N / 2);
    return goertzelDb(tail, 2200) - goertzelDb(tail, 2000); // upper sideband vs carrier
  }
  const am = await sidebandDb(0.40);       // AM: carrier + sidebands
  const ring = await sidebandDb(0.4110);   // ring null: carrier suppressed
  check("am sidebands present at full intensity", am > -30, `${am.toFixed(1)} dBc`);
  check("ring null suppresses the carrier at bias 0.4110", ring > am + 15,
    `${ring.toFixed(1)} dBc vs ${am.toFixed(1)}`);
}

// ---- KLM-64E siggen: A4 pitch from the reconciled temperament tuning ----
{
  // note 4 = A (0=F chromatic), row 1, cv at the neutral -1.62 V temperament
  // bus: fundamental ~440 Hz. Measured by autocorrelation, not peak partial -
  // the reconciled row ladder makes the octave-up partial the strongest one.
  const proc = await makeProcessor("siggen");
  proc.setParamValue("/siggen/note", 4);
  proc.setParamValue("/siggen/octave", 1);
  const N = SR * 2;
  const out = proc.render([], N)[0];
  const x = Array.from(out.subarray(SR, N));
  const mean = x.reduce((a, b) => a + b, 0) / x.length;
  const y = x.map((v) => v - mean);
  let best = 0, bestLag = 0;
  for (let lag = 30; lag < 500; lag++) {
    let acc = 0;
    for (let n = 0; n < 8000; n++) acc += y[n] * y[n + lag];
    if (acc > best) { best = acc; bestLag = lag; }
  }
  const f = SR / bestLag;
  const centsOff = 1200 * Math.log2(f / 440);
  check("siggen A4 within 30 cents of 440 Hz", Math.abs(centsOff) < 30,
    `${f.toFixed(1)} Hz (${centsOff.toFixed(0)} cents)`);
}

// ---- KLM-76 GEG: sustain ceiling + release floor via internal gate timer ----
{
  // full node-by-node trace (cab001d): OUT2 = 0.8912*env + 5.2811, spanning
  // 0.03 V floor .. 5.87 V top; release sense is INVERTED (krel = 1 fast)
  const proc = await makeProcessor("geg");
  proc.setParamValue("/geg/delay", 0);
  proc.setParamValue("/geg/attack", 0);
  proc.setParamValue("/geg/release", 1);    // 1 = FAST (traced sense)
  proc.setParamValue("/geg/gate_on", 0.1);
  proc.setParamValue("/geg/gate_off", 1.0);
  const N = SR * 2;
  const out = proc.render([], N)[0];
  const sus = out[Math.floor(0.9 * SR)];    // mid-sustain
  const floor = out[N - 1];                 // long after release
  check("geg sustain +5.87 V traced ceiling", Math.abs(sus - 5.867) < 0.05, `${sus.toFixed(2)} V`);
  check("geg release floor ~+0.03 V", Math.abs(floor - 0.03) < 0.1, `${floor.toFixed(3)} V`);
}

// ---- KLM-69E gate: KORG35 cutoff tracks vfc + envelope gates the channel ----
{
  async function toneRms(vfc, freq, gateOpen) {
    const proc = await makeProcessor("gate");
    proc.setParamValue("/gate/bypass_env", gateOpen ? 1 : 0);
    proc.setParamValue("/gate/bypass_nl", 1);          // linear core for gains
    proc.setParamValue("/gate/testosc_amp", 0.002);
    proc.setParamValue("/gate/testosc_freq", freq);
    proc.setParamValue("/gate/vfc", vfc);
    const N = SR;
    const out = proc.render([new Float32Array(N)], N)[0];
    let s = 0;
    for (let n = N / 2; n < N; n++) s += out[n] * out[n];
    return Math.sqrt(s / (N / 2));
  }
  // dark FC bus (-14 V) must attenuate 2 kHz far below bright (0 V)
  const bright = await toneRms(0, 2000, true);
  const dark = await toneRms(-14, 2000, true);
  check("korg35 cutoff tracks the fc bus", dark < bright / 30,
    `bright ${bright.toExponential(1)}, dark ${dark.toExponential(1)}`);
  // closed envelope (gate 0, env not bypassed) mutes the channel
  const closed = await toneRms(0, 2000, false);
  check("gate channel closed when envelope is down", closed < bright / 1000,
    `closed ${closed.toExponential(1)}`);
}

// ---- KLM-76 voltage processors: attenuverter law (exact network values) ----
{
  async function outAt(k1, vin1) {
    const proc = await makeProcessor("vp");
    proc.setParamValue("/vp/knob1", k1);
    proc.setParamValue("/vp/vin1", vin1);
    proc.setParamValue("/vp/knob2", 0.5);
    const out = proc.render([], 9600)[0];
    return out[9599];
  }
  // measured from the exact shared-bus network (SPICE-refereed by the board
  // tests): gains are NOT ideal +/-1 and the null is imperfect - that's the
  // hardware
  const cw = await outAt(1, 2);
  const ccw = await outAt(0, 2);
  const mid = await outAt(0.5, 2);
  check("vp CW gain (real network)", Math.abs(cw - 1.788) < 0.02, `${cw.toFixed(3)} V`);
  check("vp CCW inverting gain", Math.abs(ccw + 2.224) < 0.02, `${ccw.toFixed(3)} V`);
  check("vp imperfect center null", Math.abs(mid + 0.275) < 0.02, `${mid.toFixed(3)} V`);
}

// ---- composed instrument: GEG gates the voice through the whole chain ----
{
  const proc = await makeProcessor("instrument");
  proc.setParamValue("/instrument/vca/geg/gate_on", 0.1);
  proc.setParamValue("/instrument/vca/geg/gate_off", 1.0);
  proc.setParamValue("/instrument/vca/geg/delay", 0);
  proc.setParamValue("/instrument/vca/geg/attack", 0);
  proc.setParamValue("/instrument/vca/geg/release", 1);  // 1 = FAST (traced sense)
  proc.setParamValue("/instrument/siggen/note", 4);
  proc.setParamValue("/instrument/siggen/octave", 1);
  const N = SR * 2;
  const out = proc.render([], N)[0];
  const rms = (a, b) => {
    let s = 0;
    for (let n = Math.floor(a * SR); n < Math.floor(b * SR); n++) s += out[n] * out[n];
    return Math.sqrt(s / ((b - a) * SR));
  };
  const idle = rms(0, 0.08), sustain = rms(0.5, 0.9), tail = rms(1.8, 2.0);
  // idle floor is the VCA's real dark attenuation (~-33 dB per the corrected
  // CV law), not silence
  check("instrument gated by the GEG",
    sustain > 1e-3 && idle < sustain / 50 && tail < sustain / 30,
    `idle ${idle.toExponential(1)}, sustain ${sustain.toExponential(1)}, tail ${tail.toExponential(1)}`);
}

// ---- 48-voice composed instrument: chord sounds, trigger gates the GEG ----
{
  const proc = await makeProcessor("poly");
  // C major (C3 E3 G3): bits pc*4+oct, pc 0=F..7=C..11=E, oct row 2
  const chord = (1n << 30n) | (1n << 46n) | (1n << 10n);
  proc.setParamValue("/instrument_poly/poly/poly/keys_lo", Number(chord & 0xFFFFFFn));
  proc.setParamValue("/instrument_poly/poly/poly/keys_hi", Number(chord >> 24n));
  proc.setParamValue("/instrument_poly/vca/geg/trigger/nkeys", 3);
  const N = SR * 2;
  const out = proc.render([], N)[0];
  let s2 = 0;
  for (let n = SR; n < N; n++) s2 += out[n] * out[n];
  const rms = Math.sqrt(s2 / SR);

  const idle = await makeProcessor("poly");
  const outI = idle.render([], N)[0];
  let sI = 0;
  for (let n = SR; n < N; n++) sI += outI[n] * outI[n];
  const rmsI = Math.sqrt(sI / SR);
  check("poly chord through the full chain", rms > 1e-3 && rmsI < rms / 50,
    `chord ${rms.toExponential(1)}, idle ${rmsI.toExponential(1)}`);
}

// ---- full panel: every address it drives must exist in the built wasm ----
// setParamValue silently ignores unknown paths, so a typo leaves the control
// looking alive while doing nothing. This pins panel/params.js to the builds.
{
  const { PARAM, MOD_PARAM } = await import(join(webDir, "panel/params.js"));
  const addrsOf = async (boardId) => {
    const meta = JSON.parse(
      await readFile(join(webDir, boardId, "generated/dsp-meta.json"), "utf8"));
    const out = [];
    (function walk(items) {
      for (const it of items) {
        if (it.items) walk(it.items);
        else if (it.address) out.push(it.address);
      }
    })(meta.ui);
    return new Set(out);
  };

  const polyAddrs = await addrsOf("poly");
  const missing = Object.entries(PARAM).filter(([, a]) => !polyAddrs.has(a));
  check("panel instrument_poly addresses resolve", missing.length === 0,
    missing.length ? missing.map(([k, a]) => `${k} -> ${a}`).join(", ")
      : `${Object.keys(PARAM).length} addresses`);

  const MOD_DIR = { mg1: "mg1noise", modvca: "modvca", sh: "sh", vp: "vp" };
  for (const [board, addrs] of Object.entries(MOD_PARAM)) {
    const have = await addrsOf(MOD_DIR[board]);
    const bad = addrs.filter((a) => !have.has(a));
    check(`panel ${board} addresses resolve`, bad.length === 0,
      bad.length ? bad.join(", ") : `${addrs.length} addresses`);
  }

  // the keybed must be able to reach every one of the 48 hardwired channels
  const { CONTROLS } = await import(join(webDir, "panel/layout.js"));
  const KEY_LO = 41, KEY_HI = 88;
  const reached = new Set();
  for (let m = KEY_LO; m <= KEY_HI; m++) {
    reached.add(((m - KEY_LO) % 12) * 4 + (3 - Math.floor((m - KEY_LO) / 12)));
  }
  check("panel keybed reaches all 48 poly channels", reached.size === 48,
    `${reached.size}/48 channels, ${KEY_HI - KEY_LO + 1} keys`);
  check("panel layout has no duplicate control ids",
    new Set(CONTROLS.map((c) => c.id)).size === CONTROLS.length,
    `${CONTROLS.length} controls`);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
