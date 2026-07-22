# Browser bench pages

Each SPICE-validated board compiled to WebAssembly.

```
lib/        shared panel hardware: panel.css, panel.js (knobs/rockers/chips/
            keybed), scope.js (spectrum + strip-chart), faust-loader.js,
            faustwasm/ (one shared runtime copy)
resonator/  KLM-62 bench: saw keybed -> triple vactrol bandpass, spectrum scope
sh/         KLM-76 bench: noise/sine/ramp -> S&H -> patched VCO, strip-chart
test/       node-selftest.mjs - offline golden checks vs the SPICE references
boards.json manifest driving the hub and the tests
```

```bash
# regenerate a board's wasm after editing its dsp (keep only meta + wasm)
npx --yes -p @grame/faustwasm faust2wasm-ts dsp/<board>.dsp web/<board>/generated -double
rm -rf web/<board>/generated/{faustwasm,create-node.js,index.html,index.js}

# gate.dsp / instrument.dsp: faustwasm's libfaust 2.86.2 hits a wasm-codegen
# bug (SigBitCast badnode) on the gate's halfband FIR; use the local CLI:
cd web/<board>/generated && faust -lang wasm-i -double -json -o dsp-module.wasm ../../../dsp/<board>.dsp && mv dsp-module.json dsp-meta.json

# serve (modules + wasm need http, not file://; no-store so app code is never stale)
node web/tools/serve.mjs 8931

# offline golden tests (CI-able, no browser)
node web/test/node-selftest.mjs

# refresh the resonator scope's SPICE overlay after regenerating the reference
uv run python analysis/ac_analysis.py && uv run python web/tools/extract-spice-curves.py
```

**Adding a board:** compile its wasm as above, copy an existing bench dir as
a template (panel sections + controls from that board's real panel, a source
appropriate to the board, spectrum or strip scope), add it to `boards.json`,
and give it a check in `test/node-selftest.mjs`.
