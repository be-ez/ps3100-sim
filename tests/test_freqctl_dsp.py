"""Compare the Faust KLM-62D FREQUENCY CONTROL model (dsp/freqctl.dsp)
against the SPICE referee (netlists/klm62d-freqctl.cir).

Comparison method (a DC control-law board does not fit the impulse
harness): both simulators are evaluated at the SAME grid of control
settings - ngspice via an operating-point run of the deck, the Faust
model rendered offline with tests/impulse_driver.cpp and read after its
C401 pole has settled - and the TEMPERAMENT BUS voltages are compared
directly. The DSP implements the same fixed-point law with the deck's
device constants, so agreement is expected at the millivolt level.

The AC side (C401's 1.59 kHz pole, the only dynamic the DSP carries) is
validated by step-settling: the DSP output must move with the documented
one-pole time constant, and the servo-dropout shelf that SPICE shows
above ~430 Hz is intentionally absent (control-rate board; see the model
doc)."""

import shutil
from pathlib import Path

import pytest

from tests.test_dsp_vs_spice import FS, REPO, build_driver, render
from tests.test_freqctl_spice import run_op

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

# tolerance budget: neglected Q401-right
# base-current loading of the 98R base Thevenin (< 2.5 mV on the bus at the
# 40 uA top of the grid), neglected Q402 base current (< 0.1 mV), op-amp
# finite-gain residuals (uV) -> 6 mV bound
BUS_TOL = 6e-3

SETTLE = 4096  # samples; >> C401's 1.59 kHz pole (tau ~ 5 samples at 48k)


@pytest.fixture(scope="session")
def freqctl_bin() -> Path:
    return build_driver(REPO / "dsp" / "freqctl.dsp", "freqctl_ir")


def render_bus(freqctl_bin, **controls) -> float:
    args = [f"{k}={v}" for k, v in controls.items()]
    out = render(freqctl_bin, *args, n=SETTLE)
    return float(out[-1])


# grid spans the panel-usable range of every control: ~2 decades of Ic
# (bus -0.6 .. -4.7 V), both trimmers end to end, and a regulated load
CASES = [
    dict(),
    dict(coarse=14.9),
    dict(coarse=-14.9),
    dict(fine=-14.9),
    dict(mod1=1.0),
    dict(mod1=-1.0, mod2=-0.5),
    dict(modr1=0.4, modr2=0.3),
    dict(modr1=-0.5),
    dict(ttune=0.0),
    dict(ttune=1.0),
    dict(lin=0.0),
    dict(lin=1.0),
    dict(coarse=-10.0, fine=10.0, mod1=2.0, ttune=0.8, lin=0.2),
    dict(iload=100e-6),
]

SPICE_KEYS = dict(
    coarse="vcoarse",
    fine="vfine",
    mod1="vmod1",
    mod2="vmod2",
    modr1="vmodr1",
    modr2="vmodr2",
    ttune="ttune",
    lin="lin",
    iload="iload",
)


@pytest.mark.parametrize(
    "case", CASES, ids=lambda c: "-".join(f"{k}{v}" for k, v in c.items()) or "default"
)
def test_dsp_bus_matches_spice(freqctl_bin, case):
    spice = run_op({SPICE_KEYS[k]: v for k, v in case.items()})
    dsp = render_bus(freqctl_bin, **case)
    assert dsp == pytest.approx(spice["bus"], abs=BUS_TOL), (
        f"{case}: DSP {dsp:.4f} V vs SPICE {spice['bus']:.4f} V"
    )


def test_dsp_load_capacity_saturation(freqctl_bin):
    """Beyond Q403's sink capacity the DSP must leave regulation the same
    way SPICE does (bus relaxes toward the passive divider). Compared at
    2.5x capacity; 40 mV: the DSP's hard regulated/passive corner vs the
    soft transistor cutoff in SPICE."""
    d0 = run_op({})
    cap = -d0["bus"] / 10e3 + (d0["rail"] - d0["bus"]) / 100e3 * (100.0 / 72.0)
    il = float(2.5 * cap)
    spice = run_op(dict(iload=il))
    dsp = render_bus(freqctl_bin, iload=il)
    assert spice["bus"] - d0["bus"] < -0.05
    assert dsp == pytest.approx(spice["bus"], abs=0.04)


def test_dsp_c401_pole_settling(freqctl_bin):
    """The DSP's only dynamic is C401's 1.59 kHz one-pole on the summed
    CV: from reset (0) the output must settle with tau = 100 us, i.e. be
    within 0.1% of final after 10 tau and NOT be there after 1 tau."""
    out = render(freqctl_bin, "mod1=1.0", n=SETTLE)
    final = out[-1]
    tau = int(round(FS * 100e-6))
    assert abs(out[10 * tau] - final) < 0.001 * abs(final) + 1e-4
    assert abs(out[tau] - final) > 0.01 * abs(final)
