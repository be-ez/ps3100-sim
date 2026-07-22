// Minimal Faust runtime glue so the `faust -lang cpp` generated class builds
// without the Faust SDK headers. Matches the interface referenced by code
// generated with Faust 2.85.x (see tests/impulse_driver.cpp for the
// SDK-header-based variant used by the offline test harness).
#pragma once

#include <map>
#include <string>

#ifndef FAUSTFLOAT
#define FAUSTFLOAT double
#endif

struct Meta {
    virtual ~Meta() = default;
    virtual void declare(const char*, const char*) {}
};

struct Soundfile;

struct UI {
    virtual ~UI() = default;
    virtual void openTabBox(const char*) {}
    virtual void openHorizontalBox(const char*) {}
    virtual void openVerticalBox(const char*) {}
    virtual void closeBox() {}
    virtual void addButton(const char*, FAUSTFLOAT*) {}
    virtual void addCheckButton(const char*, FAUSTFLOAT*) {}
    virtual void addVerticalSlider(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT,
                                   FAUSTFLOAT) {}
    virtual void addHorizontalSlider(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT,
                                     FAUSTFLOAT) {}
    virtual void addNumEntry(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT,
                             FAUSTFLOAT) {}
    virtual void addHorizontalBargraph(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT) {}
    virtual void addVerticalBargraph(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT) {}
    virtual void addSoundfile(const char*, const char*, Soundfile**) {}
    virtual void declare(FAUSTFLOAT*, const char*, const char*) {}
};

class dsp {
public:
    virtual ~dsp() = default;
    virtual int getNumInputs() = 0;
    virtual int getNumOutputs() = 0;
    virtual void buildUserInterface(UI*) = 0;
    virtual int getSampleRate() = 0;
    virtual void init(int) = 0;
    virtual void instanceInit(int) = 0;
    virtual void instanceConstants(int) = 0;
    virtual void instanceResetUserInterface() = 0;
    virtual void instanceClear() = 0;
    virtual dsp* clone() = 0;
    virtual void metadata(Meta*) = 0;
    virtual void compute(int, FAUSTFLOAT**, FAUSTFLOAT**) = 0;
};

// records label -> zone pointers from buildUserInterface for direct control
struct ParamMap : UI {
    std::map<std::string, FAUSTFLOAT*> zones;

    void addCheckButton(const char* l, FAUSTFLOAT* z) override { zones[l] = z; }
    void addVerticalSlider(const char* l, FAUSTFLOAT* z, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT,
                           FAUSTFLOAT) override {
        zones[l] = z;
    }
    void addHorizontalSlider(const char* l, FAUSTFLOAT* z, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT,
                             FAUSTFLOAT) override {
        zones[l] = z;
    }
    void addNumEntry(const char* l, FAUSTFLOAT* z, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT,
                     FAUSTFLOAT) override {
        zones[l] = z;
    }

    void set(const std::string& label, FAUSTFLOAT v) {
        auto it = zones.find(label);
        if (it != zones.end()) *it->second = v;
    }

    // nullptr when the label doesn't exist in the compiled DSP - callers must
    // tolerate that (e.g. peak1..3 only appear once the dsp-accuracy stream's
    // resonator.dsp lands).
    FAUSTFLOAT* find(const std::string& label) const {
        auto it = zones.find(label);
        return it != zones.end() ? it->second : nullptr;
    }
};
