"""Compare the Faust FILTER CONTROL model (dsp/filterctl.dsp) against the
SPICE referee (netlists/klm63-filterctl.cir).

The board is purely resistive - no capacitor anywhere in the CV path - so it
shapes no dynamics and the comparison is a DC grid (per the board pipeline:
transient comparison only where a board has dynamics).  Both simulators are
evaluated at the same (vfc, vmod1, vmod2, vbal, fcadj) points, including
op-amp-clipped corners, and the FCU/FCL/FC BIAS pin voltages are compared
absolutely.  Tolerance 1e-4 V: the algebra is identical on both sides, the
residual is the deck's finite op-amp gain (1e6) whose ~1e-5-relative loop
error is divided by ~52 at the bus pins (measured < 5 uV).
"""

import shutil
from pathlib import Path

import pytest

from tests.test_dsp_vs_spice import REPO, build_driver, render
from tests.test_filterctl_spice import run_op

pytestmark = pytest.mark.skipif(
    shutil.which("faust") is None or shutil.which("c++") is None or shutil.which("ngspice") is None,
    reason="faust, c++, or ngspice not installed",
)

TOL_V = 1e-4  # finite op-amp gain residual, see module docstring

# grid spans linear operation, each clip direction, the BAL crossfade and
# the full FC ADJ trim travel
CASES = [
    dict(),
    dict(vfc=-3.0),
    dict(vfc=2.0, vmod1=-1.0, vmod2=0.5),
    dict(vmod1=3.3, vmod2=-3.3),
    dict(vbal=5.0),
    dict(vbal=-5.0, vfc=-2.0),
    dict(fcadj=0.0),
    dict(fcadj=1.0),
    dict(fcadj=1.0, vfc=-10.0),  # IC1a clipped at +13 V
    dict(vfc=15.0, vmod1=8.0),  # output amps clipped at +13 V
    dict(vfc=-15.0, vmod2=-10.0, vbal=10.0),  # output amps clipped at -13 V
]


@pytest.fixture(scope="session")
def filterctl_bin() -> Path:
    return build_driver(REPO / "dsp" / "filterctl.dsp", "filterctl_ir")


def dsp_dc(filterctl_bin: Path, outsel: int, **params) -> float:
    """Render a short buffer and take the settled last sample (the model is
    memoryless; the driver's unit impulse only perturbs sample 0)."""
    args = [f"outsel={outsel}"] + [f"{k}={v}" for k, v in params.items()]
    return float(render(filterctl_bin, *args, n=64)[-1])


@pytest.mark.parametrize(
    "case", CASES, ids=lambda c: ",".join(f"{k}={v}" for k, v in c.items()) or "quiescent"
)
def test_dc_grid_matches_spice(filterctl_bin, case):
    sp = run_op(**case)
    assert dsp_dc(filterctl_bin, 0, **case) == pytest.approx(sp["fcu"], abs=TOL_V), "FCU"
    assert dsp_dc(filterctl_bin, 1, **case) == pytest.approx(sp["fcl"], abs=TOL_V), "FCL"
    assert dsp_dc(filterctl_bin, 2, **case) == pytest.approx(sp["fcbias"], abs=TOL_V), "FC BIAS"
