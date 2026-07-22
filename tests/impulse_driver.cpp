// Minimal offline test harness for Faust DSP (used via `faust -a`).
// Feeds a unit impulse, prints output samples as text, one per line.
// Args: n=<samples> fs=<rate> <param-label>=<value> ... step:<label>=<sample>:<value>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#define FAUSTFLOAT double

#include "faust/gui/UI.h"
#include "faust/gui/meta.h"
#include "faust/gui/MapUI.h"
#include "faust/dsp/dsp.h"

<<includeIntrinsic>>

<<includeclass>>

int main(int argc, char* argv[]) {
    long n = 1L << 17;
    int fs = 48000;
    std::string step_label;
    long step_at = -1;
    double step_val = 0.0;
    std::vector<std::pair<std::string, double>> params;

    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        size_t eq = arg.find('=');
        if (eq == std::string::npos) continue;
        std::string key = arg.substr(0, eq);
        std::string val = arg.substr(eq + 1);
        if (key == "n") {
            n = atol(val.c_str());
        } else if (key == "fs") {
            fs = atoi(val.c_str());
        } else if (key.rfind("step:", 0) == 0) {
            step_label = key.substr(5);
            size_t colon = val.find(':');
            step_at = atol(val.substr(0, colon).c_str());
            step_val = atof(val.substr(colon + 1).c_str());
        } else {
            params.emplace_back(key, atof(val.c_str()));
        }
    }

    mydsp DSP;
    MapUI ui;
    DSP.buildUserInterface(&ui);
    DSP.init(fs);  // resets controls to defaults, so set params after
    for (auto& kv : params) ui.setParamValue(kv.first, kv.second);

    int nin = DSP.getNumInputs();
    int nout = DSP.getNumOutputs();
    if (nout < 1) { fprintf(stderr, "dsp has no outputs\n"); return 1; }

    std::vector<std::vector<double>> inbuf(nin, std::vector<double>(n, 0.0));
    std::vector<std::vector<double>> outbuf(nout, std::vector<double>(n, 0.0));
    if (nin > 0) inbuf[0][0] = 1.0;  // unit impulse

    std::vector<double*> inputs(nin), outputs(nout);
    auto run = [&](long start, long count) {
        for (int c = 0; c < nin; c++) inputs[c] = inbuf[c].data() + start;
        for (int c = 0; c < nout; c++) outputs[c] = outbuf[c].data() + start;
        DSP.compute((int)count, nin ? inputs.data() : nullptr, outputs.data());
    };

    if (step_at > 0 && step_at < n) {
        run(0, step_at);
        ui.setParamValue(step_label, step_val);
        run(step_at, n - step_at);
    } else {
        run(0, n);
    }

    for (long i = 0; i < n; i++) printf("%.17g\n", outbuf[0][i]);
    return 0;
}
