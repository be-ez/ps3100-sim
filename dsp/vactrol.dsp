// P873-class vactrol model (plan Phase 3b): two-pole asymmetric dynamics.
//
// Hardware context: the LED
// drive is a temperature-compensated exponential converter (~1.8 V/octave at
// the CV matrix, I_ref ~= 1.5 uA, LED current capped by 470R), so f0 is
// exponential in CV. The log-law cv2r below matches that shape end-to-end
// (f0 proportional to 1/R); calibrate rmin/rmax/volts-per-octave against the
// real sweep range when tuning by ear.
//
// Dynamics: reduction of Najnudel, Muller, Helie, Roze, "Power-balanced
// dynamic modeling of vactrols: application to a VTL5C3/2" (DAFx-23,
// dafx.de/paper-archive/2023/DAFx23_paper_50.pdf). Shockley-Read-Hall
// recombination gives two free-carrier populations (electrons / holes)
// with BIMOLECULAR recombination, qdot = -nu * q^2 (their eq. 4), and the
// cell conductance is proportional to carrier density (their eq. 7). So:
//   - attack (lit): generation-limited, roughly fixed ms-scale tau
//     (VTL5C3 turn-on ~2.5..3 ms; CdS cells a bit slower);
//   - decay (dark): instantaneous tau = k/g grows as conductance falls,
//     i.e. the model slows down as resistance rises. In resistance terms
//     each population recovers along a ~linear R ramp that settles
//     exponentially into its dark floor - the classic CdS memory tail.
// Two populations with well-separated recombination constants reproduce the
// measured two-time-scale turn-off: resistance roughly triples within a few
// ms, then creeps up over hundreds of ms .. seconds (Vactec/Perkin-Elmer
// app data as summarized by R. Holmes, richardsholmes.com vactrol page).
// One-pole state-dependent-tau prior art: Parker & D'Angelo, "A Digital
// Model of the Buchla Lowpass-Gate" (DAFx-13).
//
// Constants are a datasheet-order fit for the slow CdS P873; refine against
// recordings of a real PS3100 later.
import("stdfaust.lib");

rmin = 1e3;    // fully lit
rmax = 1e6;    // dark
gmin = 1.0 / rmax;

// CV in [0,1] -> steady-state LDR resistance (log law over the sweep range)
cv2r(cv) = rmax * pow(rmin / rmax, cv);

// --- dynamics constants (provenance in header) ---
tauGen = 0.0015;    // LED + photon flux + carrier generation build-up (s)
tauAttack = 0.0035; // generation-limited attack tau per population (s);
                    // cascade with tauGen gives t90 ~= 10 ms (VTL5C3 ~3 ms,
                    // CdS/P873 slower)
// population weights (share of total conductance) and recombination
// constants k (decay tau = k / g, units S*s): kFast sets the quick partial
// recovery (initial dark ramp ~ 1/kFast = 6.3 MOhm/s), kSlow the seconds-
// scale memory tail (~2 MOhm/s ramp, dark-floor settle k/(w*gmin) ~= 1.7 s)
wFast = 0.70;  kFast = 1.6e-7;
wSlow = 0.30;  kSlow = 5.0e-7;

// Target LDR resistance (ohms) -> LDR resistance through the two-pole
// asymmetric lag. Law/dynamics split is deliberate: boards with their own
// LED drive law (KLM-76 VCA, KLM-63 MOD-VCA, ...) enter here with the
// resistance target directly; cv2r above is the KLM-62D law only.
// Each population p holds its share w of the total conductance; state is
// offset by w*gmin so the vactrol powers up dark (R = rmax), not at 0 ohms.
vactrolR(rTarget) = 1.0 / (population(wFast, kFast) + population(wSlow, kSlow))
with {
    gTarget = 1.0 / rTarget;
    // pole 1 (shared): generation build-up, fast and symmetric
    gDrive = gmin + si.smooth(ba.tau2pole(tauGen))(gTarget - gmin);
    // pole 2 (per population): fixed-tau attack, bimolecular decay whose
    // tau = k/g stretches as the population empties (memory effect)
    population(w, k) = w * gmin + (recomb ~ _)
    with {
        recomb(s) = s + (target - s) * coef
        with {
            target = w * (gDrive - gmin);
            g = s + w * gmin;                       // absolute conductance
            tau = ba.if(target > s, tauAttack, k / g);
            coef = 1.0 - exp(-1.0 / (ma.SR * tau));
        };
    };
};

// CV in [0,1] -> LDR resistance: KLM-62D log law, then the shared dynamics.
vactrol(cv) = vactrolR(cv2r(cv));

// demo/test process: normalized resistance for a given CV
process = vactrol(hslider("cv", 0.0, 0.0, 1.0, 0.001)) / rmax;
