// Parameter addresses the full panel drives inside the composed
// instrument_poly wasm. Kept in its own module so web/test/node-selftest.mjs
// can check every address against the build's dsp-meta.json: a typo here
// otherwise fails silently (Faust's setParamValue ignores unknown paths, so
// the control just does nothing and the panel still looks alive).
const P = "/instrument_poly/";

export const PARAM = {
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

// Addresses the panel drives on the standalone modulation-board wasm nodes.
export const MOD_PARAM = {
  mg1: ["/mg1_noise/outsel", "/mg1_noise/vfc1"],
  modvca: ["/modvca/mg2_rate", "/modvca/probe"],
  sh: ["/sh/clock", "/sh/testmode"],
  vp: ["/vp/knob1", "/vp/knob2", "/vp/vin1", "/vp/vin2", "/vp/monitor"],
};
