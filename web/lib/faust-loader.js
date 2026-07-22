// Generic loader for a board's faustwasm output (dsp-meta.json +
// dsp-module.wasm in `baseUrl`). Shares one copy of the faustwasm runtime.
import { FaustMonoDspGenerator } from "./faustwasm/index.js";

export async function loadFaustNode(audioContext, name, baseUrl) {
  const dspMeta = await (await fetch(`${baseUrl}/dsp-meta.json`)).json();
  const dspModule = await WebAssembly.compile(
    await (await fetch(`${baseUrl}/dsp-module.wasm`)).arrayBuffer());
  const generator = new FaustMonoDspGenerator();
  const dsp = { module: dspModule, json: JSON.stringify(dspMeta), soundfiles: {} };
  try {
    return await generator.createNode(audioContext, name, dsp);
  } catch (e) {
    // AudioWorklet needs a secure context; plain http over LAN (e.g. a phone
    // hitting a dev box) is not one. ScriptProcessor still works there.
    console.warn("AudioWorklet unavailable, falling back to ScriptProcessor", e);
    // surface the fallback so pages can warn: SP runs on the main thread and
    // cannot keep up with the heavy composed builds (48-voice core etc.)
    if (typeof window !== "undefined") window.__spFallback = (window.__spFallback ?? 0) + 1;
    return generator.createNode(audioContext, name, dsp, true, 1024);
  }
}
