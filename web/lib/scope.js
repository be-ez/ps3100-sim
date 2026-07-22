// Shared scope drawing: amber-phosphor traces on the panel display.
// spectrumScope - log-frequency magnitude from an AnalyserNode.
// stripScope   - slow strip-chart of a sampled value (CV staircases, lag).

const GRID = "rgba(216,211,198,0.08)";
const TRACE = "rgba(255,182,72,0.9)";
const GLOW = "rgba(255,182,72,0.5)";

function grid(ctx, w, h, vlinesX) {
  ctx.strokeStyle = GRID;
  ctx.lineWidth = 1;
  for (const x of vlinesX) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let i = 1; i < 4; i++) {
    const y = (i / 4) * h;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
}

function stroke(ctx) {
  ctx.strokeStyle = TRACE;
  ctx.lineWidth = 1.6;
  ctx.shadowColor = GLOW;
  ctx.shadowBlur = 6;
  ctx.stroke();
  ctx.shadowBlur = 0;
}

// onFrame(dtSeconds) runs inside the rAF loop before drawing (MG, lamps…).
// overlay() may return {freq:[Hz...], db:[dB...], label} - drawn as a faint
// dashed reference trace on its own fixed dB mapping (shape and peak
// positions, not absolute level, are the comparison).
export function spectrumScope(canvas, getAnalyser, getSampleRate,
    { fLo = 40, fHi = 16000, dbLo = -90, dbHi = -10, onFrame, overlay,
      ovDbLo = -42, ovDbHi = 8 } = {}) {
  const ctx = canvas.getContext("2d");
  let freqData = null, lastFrame = 0;

  function draw(now) {
    requestAnimationFrame(draw);
    const dt = Math.min(0.1, (now - lastFrame) / 1000 || 0.016);
    lastFrame = now;
    onFrame?.(dt);

    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    grid(ctx, w, h,
      [100, 1000, 10000].map((f) => (Math.log(f / fLo) / Math.log(fHi / fLo)) * w));

    const ov = overlay?.();
    if (ov) {
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < ov.freq.length; i++) {
        const f = ov.freq[i];
        if (f < fLo || f > fHi) continue;
        const x = (Math.log(f / fLo) / Math.log(fHi / fLo)) * w;
        const db = Math.max(ovDbLo, Math.min(ovDbHi, ov.db[i]));
        const y = h - ((db - ovDbLo) / (ovDbHi - ovDbLo)) * (h - 6) - 3;
        started ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        started = true;
      }
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "rgba(216,211,198,0.45)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
      if (ov.label) {
        ctx.fillStyle = "rgba(216,211,198,0.5)";
        ctx.font = "9px Helvetica, Arial, sans-serif";
        ctx.fillText(ov.label, 8, 12);
      }
    }

    const analyser = getAnalyser();
    if (!analyser) return;
    if (!freqData) freqData = new Float32Array(analyser.frequencyBinCount);
    analyser.getFloatFrequencyData(freqData);
    const binHz = getSampleRate() / analyser.fftSize;

    ctx.beginPath();
    for (let px = 0; px <= w; px += 2) {
      const f = fLo * (fHi / fLo) ** (px / w);
      const bin = Math.min(freqData.length - 1, Math.round(f / binHz));
      const db = Math.max(dbLo, Math.min(dbHi, freqData[bin]));
      const y = h - ((db - dbLo) / (dbHi - dbLo)) * (h - 6) - 3;
      px === 0 ? ctx.moveTo(px, y) : ctx.lineTo(px, y);
    }
    stroke(ctx);
  }
  requestAnimationFrame(draw);
}

// getValue() -> number|null each frame; the chart scrolls left over `seconds`
export function stripScope(canvas, getValue,
    { min = -6, max = 6, seconds = 8, onFrame } = {}) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const samples = new Float32Array(w).fill(NaN);
  let head = 0, lastFrame = 0, acc = 0;
  const secPerPx = seconds / w;

  function draw(now) {
    requestAnimationFrame(draw);
    const dt = Math.min(0.1, (now - lastFrame) / 1000 || 0.016);
    lastFrame = now;
    onFrame?.(dt);

    const v = getValue();
    acc += dt;
    while (acc >= secPerPx) {          // advance the chart at fixed px rate
      acc -= secPerPx;
      samples[head] = v === null ? NaN : v;
      head = (head + 1) % w;
    }

    ctx.clearRect(0, 0, w, h);
    grid(ctx, w, h, [w * 0.25, w * 0.5, w * 0.75]);

    ctx.beginPath();
    let started = false;
    for (let px = 0; px < w; px++) {
      const s = samples[(head + px) % w];
      if (Number.isNaN(s)) { started = false; continue; }
      const y = h - ((Math.min(max, Math.max(min, s)) - min) / (max - min)) * (h - 6) - 3;
      started ? ctx.lineTo(px, y) : ctx.moveTo(px, y);
      started = true;
    }
    stroke(ctx);
  }
  requestAnimationFrame(draw);
}
