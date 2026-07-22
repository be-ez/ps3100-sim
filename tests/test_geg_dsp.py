"""Compare the Faust GEG model (dsp/geg.dsp) against the SPICE referee
(netlists/klm76-geg.cir).

Comparison method (an envelope generator does not fit the impulse-response
harness of test_dsp_vs_spice.py): both simulators are driven with the SAME
gate timing at matched delay/attack/release pot settings - ngspice via the
deck's PULSE source, the Faust model via its gate_on/gate_off test hooks
rendered offline with tests/impulse_driver.cpp - and the resulting envelope
SHAPES are compared through segment metrics:

  - t_on:     gate-on -> 10 % rise (delay stage + attack overhead)
  - attack:   10-90 % rise duration
  - release:  90-10 % fall duration after gate-off
  - sustain:  flat-top level (Q104 saturation ceiling through the OUT2
              level shifter; current-independent per the traced netlist)
  - floor:    resting level (Q106 saturation floor, trimmed to ~0 V at OUT2)

Segment curvature is covered by test_geg_spice.py's linearity assertion on
the SPICE side and by construction (linear integrator) on the DSP side.
The DSP's steering-law constants were fitted to ngspice sweeps of all three
pots of the traced netlist (fit residual <= 0.7 %); tolerances below leave
headroom for the fit residual plus the DSP's hard-corner approximation of
the soft saturation corners (the last ~0.2 V of each ramp rounds in SPICE).
"""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tests.test_dsp_vs_spice import FS, REPO, build_driver, render
from tests.test_geg_spice import measure, run_geg

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

TIME_RTOL = 0.08  # segment-duration agreement (worst measured ~5 %)
TIME_ABS = 1.5e-3  # absolute floor: comparator lag + 0.5 ms SPICE print step
LEVEL_TOL = 0.10  # sustain/floor agreement in volts (measured < 1 mV)


@pytest.fixture(scope="session")
def geg_bin() -> Path:
    return build_driver(REPO / "dsp" / "geg.dsp", "geg_ir")


def render_geg(geg_bin, kdel, katt, krel, gon, goff, tstop):
    n = int(tstop * FS)
    out = render(
        geg_bin,
        f"delay={kdel}",
        f"attack={katt}",
        f"release={krel}",
        f"gate_on={gon}",
        f"gate_off={goff}",
        n=n,
    )
    return {"t": np.arange(n) / FS, "out2": out}


# settings span the fast, mid, and slow-attack/slow-release regions of the
# pots while keeping ngspice transients short; slower settings only stretch
# the same fitted laws (validated up to ~11 s in the fitting sweeps).
# NOTE the traced release sense: krel = 1 is FAST (see dsp/geg.dsp header).
CASES = [
    dict(kdel=0.0, katt=0.3, krel=0.8, gon=0.05, goff=0.5, tstop=0.8),
    dict(kdel=0.4, katt=0.6, krel=0.6, gon=0.05, goff=0.9, tstop=1.5),
    dict(kdel=0.0, katt=0.75, krel=0.5, gon=0.05, goff=2.0, tstop=3.6),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"d{c['kdel']}a{c['katt']}r{c['krel']}")
def test_dsp_envelope_matches_spice(geg_bin, case):
    sp = measure(
        run_geg(case["kdel"], case["katt"], case["krel"], case["gon"], case["goff"], case["tstop"]),
        case["gon"],
        case["goff"],
    )
    dp = measure(render_geg(geg_bin, **case), case["gon"], case["goff"])
    for key in ["t_on", "attack", "release"]:
        assert abs(dp[key] - sp[key]) < TIME_RTOL * sp[key] + TIME_ABS, (
            f"{key}: dsp {dp[key] * 1e3:.2f} ms vs spice {sp[key] * 1e3:.2f} ms"
        )
    for key in ["sustain", "floor"]:
        assert dp[key] == pytest.approx(sp[key], abs=LEVEL_TOL), key


def test_gate_control_without_test_hooks(geg_bin):
    """The plain `gate` control must drive the envelope too: stepping it on
    mid-render (impulse_driver step feature) produces the attack."""
    n = FS  # 1 s
    step_at = 4800
    out = subprocess.run(
        [str(geg_bin), f"n={n}", f"fs={FS}", "attack=0.3", "release=0.3", f"step:gate={step_at}:1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    v = np.array(out.split(), dtype=float)
    assert v[:step_at].max() < 0.05, "envelope moved before the gate"
    assert v[-1] > 4.5, "sustain not reached after gate on"
