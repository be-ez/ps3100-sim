"""Validate the KLM-76 Voltage Processors netlist against hand theory
:
exact linear-network DC/AC solve vs ngspice, the ganged attenuverter law
OUT = (2g-1)*Vin with its R236 offset cancellation, the 159 Hz lag on the
non-inverting path only, clipping levels, output/input impedance, channel
symmetry and the shared -5 V bus crosstalk."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
NETLIST = REPO / "netlists" / "klm76-vp.cir"

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# component values (scan page 0019; keep in sync with the netlist)
R222 = 1e6  # R227
R223 = 100e3  # R228
R224 = 1e6  # R229
C204 = 1e-9  # C205, "0.001" uF
R226 = 220.0  # R231
R234 = R235 = 1e6  # R240/R241
R236 = 3e6  # R242
R237 = 1e6  # R243
R239 = 220.0  # R245
RPOT_DEFAULT = 50e3  # NOT on the sheet (panel pot) - flagged assumption
RTH_REF = 2e3 * 1e3 / 3e3  # both 2K/1K dividers: 667 ohm Thevenin
VREF_P = -15.0 / 3.0  # left divider tap: pot cold ends, -5.000 V
VREF_W = -14.9 / 3.0  # R246/R247 tap: R223/R228 pulldowns, -4.967 V
VOFF = 14.9 / 3.0  # R236 injection referred to the 1M feedback
F_LAG = 1.0 / (2 * np.pi * R224 * C204)  # 159.15 Hz
VCLIP = 13.4  # house 4558 swing on +/-14.9 V rails
DC_STEP = 0.1  # netlist .control sweep step, -20..20


def run_vp(
    ka1: float = 0.0,
    kb1: float = 1.0,
    ka2: float = 0.0,
    kb2: float = 1.0,
    vin1: float = 0.0,
    vin2: float = 0.0,
    rpot: float = RPOT_DEFAULT,
    rsrc: float = 1e-3,
    rload: float = 1e9,
    ngspice: str = "ngspice",
) -> dict[str, np.ndarray]:
    """One full deck run: both DC sweeps and the channel-1 AC analysis."""
    deck = NETLIST.read_text()
    deck = re.sub(
        r"^\.param .*$",
        f".param vin1={vin1} vin2={vin2} ka1={ka1} kb1={kb1} ka2={ka2} kb2={kb2}"
        f" rpot={rpot} rsrc={rsrc} rload={rload}",
        deck,
        count=1,
        flags=re.MULTILINE,
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "deck.cir").write_text(deck)
        proc = subprocess.run(
            [ngspice, "-b", "deck.cir"], cwd=tmpdir, capture_output=True, text=True
        )
        out = {}
        for name in ["vp_dc1", "vp_dc2", "vp_ac"]:
            f = tmpdir / f"{name}.txt"
            if not f.exists():
                raise RuntimeError(f"ngspice produced no {name}:\n{proc.stdout}\n{proc.stderr}")
            out[name] = np.loadtxt(f)
    dc1, dc2, ac = out["vp_dc1"], out["vp_dc2"], out["vp_ac"]
    # wrdata layout: [scale, var, scale, var, ...]
    return {
        "vin": dc1[:, 0],
        "pout1": dc1[:, 1],
        "pout2_x": dc1[:, 3],  # ch2 out while ch1 is swept (crosstalk)
        "wa1": dc1[:, 5],
        "wb1": dc1[:, 7],
        "lm1o": dc1[:, 9],
        "refp": dc1[:, 11],
        "in1": dc1[:, 13],
        "iin1": dc1[:, 15],
        "pout1_x": dc2[:, 1],  # ch1 out while ch2 is swept
        "pout2": dc2[:, 3],
        "freq": ac[:, 0],
        "ac1_db": ac[:, 1],
        "ac2_db": ac[:, 3],
    }


def theory(
    vin1: complex,
    vin2: complex,
    ka1: float,
    kb1: float,
    ka2: float,
    kb2: float,
    rpot: float = RPOT_DEFAULT,
    rsrc: float = 1e-3,
    rload: float = 1e9,
    f: float = 0.0,
    refs_on: bool = True,
) -> tuple[complex, complex]:
    """Exact linear solve of the two-channel network (ideal op-amps).

    Independent derivation from the traced schematic: nodes are the two
    input nodes, the two pot-B top nodes (behind R226), the four wipers
    (each loaded by 1M into the mixer virtual ground) and the two shared
    reference taps.  The input amps enter as dependent sources
    lm = -H(s)*in with H the R224||C204 over R222 inverting gain.
    f=0 gives the DC operating point; refs_on=False zeroes the reference
    and offset sources for AC (small-signal) solutions.
    """
    s = 2j * np.pi * f
    h = (R224 / R222) / (1.0 + s * R224 * C204)  # lag on the inverting amp
    # node order: in1 tb1 wb1 wa1 in2 tb2 wb2 wa2 refp refw
    n = 10
    g = np.zeros((n, n), dtype=complex)
    rhs = np.zeros(n, dtype=complex)
    vref_p = VREF_P if refs_on else 0.0
    vref_w = VREF_W if refs_on else 0.0

    def stamp(a, b, r):
        y = 1.0 / r
        if a >= 0:
            g[a, a] += y
        if b >= 0:
            g[b, b] += y
        if a >= 0 and b >= 0:
            g[a, b] -= y
            g[b, a] -= y

    for ch, (i_in, i_tb, i_wb, i_wa, ka, kb, vsrc) in enumerate(
        [(0, 1, 2, 3, ka1, kb1, vin1), (4, 5, 6, 7, ka2, kb2, vin2)]
    ):
        ra1 = max(rpot * (1 - ka), 1.0)
        ra2 = max(rpot * ka, 1.0)
        rb1 = max(rpot * (1 - kb), 1.0)
        rb2 = max(rpot * kb, 1.0)
        stamp(i_in, -1, rsrc)  # to source (RHS below)
        rhs[i_in] += vsrc / rsrc
        stamp(i_in, -1, R222)  # into IC23 virtual ground
        stamp(i_in, 9, R223)
        stamp(i_in, 3 if ch == 0 else 7, ra1)
        # tb node: R226 from the dependent source lm = -h*v(in)
        stamp(i_tb, -1, R226)
        g[i_tb, i_in] += h / R226  # move -h*v(in)/R226 to the LHS
        stamp(i_tb, i_wb, rb1)
        stamp(i_wb, 8, rb2)
        stamp(i_wb, -1, R234)  # into mixer virtual ground
        stamp(i_wa, 8, ra2)
        stamp(i_wa, -1, R235)
    stamp(8, -1, RTH_REF)
    rhs[8] += vref_p / RTH_REF
    stamp(9, -1, RTH_REF)
    rhs[9] += vref_w / RTH_REF
    v = np.linalg.solve(g, rhs)
    off = VOFF if refs_on else 0.0
    outs = []
    for i_wb, i_wa in [(2, 3), (6, 7)]:
        out = -(v[i_wb] + v[i_wa] + off)
        outs.append(out * rload / (rload + R239))
    return outs[0], outs[1]


def ideal_law(g: float, vin: float) -> float:
    """Ganged attenuverter with the R236 pedestal cancellation, no loading."""
    return (2 * g - 1) * vin + (5.0 - VOFF)


def j(v: float) -> int:
    return int(round((v + 20.0) / DC_STEP))


@pytest.fixture(scope="module")
def default_run():
    return run_vp()


KNOB_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


@pytest.mark.parametrize("rpot", [10e3, 50e3, 200e3])
@pytest.mark.parametrize("g", KNOB_GRID)
def test_dc_matches_theory(g, rpot):
    """SPICE vs the exact linear solve over the panel range, both channels,
    counter-driven inputs to exercise the shared-bus coupling. Agreement is
    numerical only (ideal-vs-tanh op-amp linearization)."""
    res = run_vp(ka1=1 - g, kb1=g, ka2=1 - g, kb2=g, vin2=-2.5, rpot=rpot)
    for vin in [-5.0, -2.5, 0.0, 2.5, 5.0]:
        th1, th2 = theory(vin, -2.5, 1 - g, g, 1 - g, g, rpot=rpot)
        assert abs(res["pout1"][j(vin)] - th1.real) < 1e-3
        assert abs(res["pout2_x"][j(vin)] - th2.real) < 1e-3


def test_general_two_section_law():
    """Un-ganged sections (ka + kb != 1): the theory solve must still track
    SPICE - this is what protects the netlist against a silent gang-only
    simplification."""
    res = run_vp(ka1=0.8, kb1=0.7, ka2=0.1, kb2=0.2, vin2=3.0)
    for vin in [-5.0, 0.0, 5.0]:
        th1, th2 = theory(vin, 3.0, 0.8, 0.7, 0.1, 0.2)
        assert abs(res["pout1"][j(vin)] - th1.real) < 1e-3
        assert abs(res["pout2_x"][j(vin)] - th2.real) < 1e-3


@pytest.mark.parametrize("g", KNOB_GRID)
def test_attenuverter_law(g):
    """The panel law: OUT = (2g-1)*Vin + 0.03 V. The only deviation from
    ideal is pot loading of the 667R reference Thevenin and of the wipers by
    the 1M mixer inputs: exact-solve worst case 0.33 V over the panel grid
    at the assumed rpot = 50k (1.08 V at 10k, 0.28 V at 100k - rpot is not
    on the sheet, so the bound documents the assumption, not the hardware)."""
    res = run_vp(ka1=1 - g, kb1=g, ka2=1 - g, kb2=g)
    for vin in [-5.0, -2.5, 0.0, 2.5, 5.0]:
        assert abs(res["pout1"][j(vin)] - ideal_law(g, vin)) < 0.35
    # slope over the panel range is the attenuverter gain 2g-1
    slope = (res["pout1"][j(5.0)] - res["pout1"][j(-5.0)]) / 10.0
    assert slope == pytest.approx(2 * g - 1, abs=0.02)


def test_offset_cancellation():
    """The R236 3M / +14.9 V injection (-4.967 V at the output) exists to
    cancel the two pot sections' -5 V pedestals, which sum to one 5 V unit
    exactly when ka + kb = 1 (the opposite-wired gang): at Vin = 0 the
    output stays near zero across the whole knob travel."""
    for g in KNOB_GRID:
        res = run_vp(ka1=1 - g, kb1=g, ka2=1 - g, kb2=g)
        assert abs(res["pout1"][j(0.0)]) < 0.30
    # ...and the cancellation really is the ganged-wiring special case:
    # same-direction sections (ka = kb = 0) leave the full pedestal
    res = run_vp(ka1=0.0, kb1=0.0, ka2=0.0, kb2=0.0)
    assert res["pout1"][j(0.0)] > 4.0


def test_lag_only_on_noninverting_path(default_run):
    """C204 1n across R224 1M lags the inverted amp at 159 Hz. Full CW
    (g=1, non-inverting) the chain shows the one-pole corner; full CCW
    (g=0) the signal reaches the mixer through pot section A only - flat."""
    f, db = default_run["freq"], default_run["ac1_db"]
    mid = np.interp(10.0, f, db)
    lo = f < 2000.0
    # db falls with f: reverse for np.interp's increasing-xp requirement
    fc = np.interp(mid - 3.0103, db[lo][::-1], f[lo][::-1])
    assert fc == pytest.approx(F_LAG, rel=0.03)
    inv = run_vp(ka1=1.0, kb1=0.0, ka2=1.0, kb2=0.0)
    band = (inv["freq"] >= 1.0) & (inv["freq"] <= 20e3)
    # not perfectly flat: the antiphase B-section bus injection is lagged,
    # so its cancellation fades above 159 Hz - a +0.11 dB shelf (exact
    # solve agrees), not a loosened claim about the direct path
    assert np.ptp(inv["ac1_db"][band]) < 0.15


def test_center_knob_ac_leak():
    """At knob center the DC gain nulls but the lag asymmetry leaks HF as a
    first-order HPF toward the inverting phase: theory |0.5*(H(jw)-1)|,
    i.e. -6 dB at HF relative to unity, rolling off toward LF."""
    res = run_vp(ka1=0.5, kb1=0.5, ka2=0.5, kb2=0.5)
    f, db = res["freq"], res["ac1_db"]
    hf = np.interp(20e3, f, db)
    lf = np.interp(10.0, f, db)
    assert hf == pytest.approx(20 * np.log10(0.5), abs=0.35)  # pot loading trims gain
    assert lf < hf - 20.0


def test_clipping(default_run):
    """4558 output swing (house rail-limited op-amp model, +/-13.4 V), well
    outside the +/-5 V panel range. At g=1 the negative side is the mixer's
    own clip (hard flat); the positive side is the INPUT amp clipping at
    -13.4 first, seen through the pot - so it plateaus ~13.1 V with a small
    residual slope from the un-clipped pot-A feedthrough."""
    out = default_run["pout1"]
    assert 13.0 < out.max() < 13.5
    assert 13.0 < -out.min() < 13.5
    assert abs(out[j(15.0)] - out[j(20.0)]) < 0.1
    assert abs(out[j(-15.0)] - out[j(-20.0)]) < 1e-3


def test_output_impedance():
    """R239/R245 220R is the only output impedance (op-amp closed loop):
    a matched 220R load halves the output."""
    unloaded = run_vp()["pout1"][j(5.0)]
    loaded = run_vp(rload=220.0)["pout1"][j(5.0)]
    assert loaded / unloaded == pytest.approx(0.5, abs=0.01)


def test_input_impedance(default_run):
    """Input node loading: R223 100k to the -4.97 tap, R222 1M into virtual
    ground, and pot section A (g-dependent). At g=1 (section A full toward
    the -5 bus): 100k || 1M || ~(rpot + bus) ~= 31k."""
    i5, i0 = default_run["iin1"][j(5.0)], default_run["iin1"][j(0.0)]
    zin = 5.0 / abs(i5 - i0)
    ra_path = RPOT_DEFAULT + 1.0 / (1 / RTH_REF + 3 / RPOT_DEFAULT)  # bus via other sections
    zth = 1.0 / (1 / R223 + 1 / R222 + 1 / ra_path)
    assert zin == pytest.approx(zth, rel=0.02)


def test_channels_identical(default_run):
    """Sweeping ch2 with identical settings must mirror ch1 exactly."""
    assert np.allclose(default_run["pout1"], default_run["pout2"], atol=1e-6)


def test_bus_crosstalk(default_run):
    """The shared -5.00 V pot bus (667R Thevenin) couples the channels, but
    the ganged opposite sections inject ANTIPHASE signal currents, so DC
    crosstalk cancels almost completely (< -60 dB); what survives is an AC
    leak where the 159 Hz lag unbalances the pair - a ~-38 dB plateau above
    ~1 kHz at 50k pots. Quantified so the DSP's shared-bus solve stays
    honest."""
    x = default_run["pout2_x"]
    lin = slice(j(-5.0), j(5.0) + 1)
    gain = np.polyfit(default_run["vin"][lin], x[lin], 1)[0]
    assert abs(gain) < 1e-3
    f, xdb = default_run["freq"], default_run["ac2_db"]
    assert -45.0 < np.interp(20e3, f, xdb) < -30.0
    assert np.interp(10.0, f, xdb) < -55.0


def test_unpatched_manual_law():
    """Nothing in the jack (rsrc = 1G): the input node floats to ~-4.8 V
    through R223 / pot section A, so the knob becomes a bipolar manual CV
    of ~ -(2g-1)*4.8 V - CCW positive, CW negative, center ~0."""
    outs = []
    for g in [0.0, 0.5, 1.0]:
        res = run_vp(ka1=1 - g, kb1=g, ka2=1 - g, kb2=g, rsrc=1e9)
        outs.append(res["pout1"][j(0.0)])
    assert 4.2 < outs[0] < 5.2
    assert abs(outs[1]) < 0.5
    assert -5.2 < outs[2] < -4.2
