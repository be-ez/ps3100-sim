/* ------------------------------------------------------------
name: "resonator"
Code generated with Faust 2.85.9 (https://faust.grame.fr)
Compilation options: -lang cpp -fpga-mem-th 4 -ct 1 -cn ResonatorDSP -es 1 -mcd 16 -mdd 1024 -mdy 33 -double -ftz 0
------------------------------------------------------------ */

#ifndef  __ResonatorDSP_H__
#define  __ResonatorDSP_H__

#ifndef FAUSTFLOAT
#define FAUSTFLOAT float
#endif 

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <math.h>

#ifndef FAUSTCLASS 
#define FAUSTCLASS ResonatorDSP
#endif

#ifdef __APPLE__ 
#define exp10f __exp10f
#define exp10 __exp10
#endif

#if defined(_WIN32)
#define RESTRICT __restrict
#else
#define RESTRICT __restrict__
#endif

static double ResonatorDSP_faustpower2_f(double value) {
	return value * value;
}

class ResonatorDSP : public dsp {
	
 private:
	
	int fSampleRate;
	double fConst0;
	double fConst1;
	double fConst2;
	double fConst3;
	double fConst4;
	double fConst5;
	double fConst6;
	double fConst7;
	double fConst8;
	double fConst9;
	double fRec0[3];
	int IOTA0;
	double fVec0[32];
	FAUSTFLOAT fHslider0;
	FAUSTFLOAT fHslider1;
	double fConst10;
	double fConst11;
	FAUSTFLOAT fEntry0;
	FAUSTFLOAT fCheckbox0;
	double fConst12;
	double fConst13;
	FAUSTFLOAT fHslider2;
	double fRec18[2];
	double fConst14;
	double fRec17[2];
	double fRec19[2];
	FAUSTFLOAT fEntry1;
	double fConst15;
	double fConst16;
	double fRec13[2];
	double fRec14[2];
	FAUSTFLOAT fHslider3;
	double fRec9[2];
	double fRec10[2];
	FAUSTFLOAT fHslider4;
	double fRec5[2];
	double fRec6[2];
	double fRec1[2];
	double fRec2[2];
	double fRec20[2];
	double fRec21[2];
	double fRec24[2];
	double fRec25[2];
	double fVec1[32];
	double fVec2[32];
	
 public:
	ResonatorDSP() {
	}
	
	ResonatorDSP(const ResonatorDSP&) = default;
	
	virtual ~ResonatorDSP() = default;
	
	ResonatorDSP& operator=(const ResonatorDSP&) = default;
	
	void metadata(Meta* m) { 
		m->declare("basics.lib/name", "Faust Basic Element Library");
		m->declare("basics.lib/version", "1.22.0");
		m->declare("compile_options", "-lang cpp -fpga-mem-th 4 -ct 1 -cn ResonatorDSP -es 1 -mcd 16 -mdd 1024 -mdy 33 -double -ftz 0");
		m->declare("filename", "resonator.dsp");
		m->declare("filters.lib/fir:author", "Julius O. Smith III");
		m->declare("filters.lib/fir:copyright", "Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m->declare("filters.lib/fir:license", "MIT-style STK-4.3 license");
		m->declare("filters.lib/iir:author", "Julius O. Smith III");
		m->declare("filters.lib/iir:copyright", "Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m->declare("filters.lib/iir:license", "MIT-style STK-4.3 license");
		m->declare("filters.lib/lowpass0_highpass1", "Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m->declare("filters.lib/name", "Faust Filters Library");
		m->declare("filters.lib/tf2:author", "Julius O. Smith III");
		m->declare("filters.lib/tf2:copyright", "Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m->declare("filters.lib/tf2:license", "MIT-style STK-4.3 license");
		m->declare("filters.lib/tf2s:author", "Julius O. Smith III");
		m->declare("filters.lib/tf2s:copyright", "Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m->declare("filters.lib/tf2s:license", "MIT-style STK-4.3 license");
		m->declare("filters.lib/version", "1.7.1");
		m->declare("maths.lib/author", "GRAME");
		m->declare("maths.lib/copyright", "GRAME");
		m->declare("maths.lib/license", "LGPL with exception");
		m->declare("maths.lib/name", "Faust Math Library");
		m->declare("maths.lib/version", "2.9.0");
		m->declare("name", "resonator");
		m->declare("platform.lib/name", "Generic Platform Library");
		m->declare("platform.lib/version", "1.3.0");
		m->declare("signals.lib/name", "Faust Routing Library");
		m->declare("signals.lib/version", "1.6.0");
	}

	virtual int getNumInputs() {
		return 1;
	}
	virtual int getNumOutputs() {
		return 1;
	}
	
	static void classInit(int sample_rate) {
	}
	
	virtual void instanceConstants(int sample_rate) {
		fSampleRate = sample_rate;
		fConst0 = std::min<double>(1.92e+05, std::max<double>(1.0, static_cast<double>(fSampleRate)));
		fConst1 = std::tan(149.72830587008954 / fConst0);
		fConst2 = ResonatorDSP_faustpower2_f(fConst1);
		fConst3 = 1.0 / fConst1;
		fConst4 = (fConst3 + 2.970442628930023) / fConst1 + 1.0;
		fConst5 = fConst2 * fConst4;
		fConst6 = 1.0 / fConst5;
		fConst7 = 1.0 / fConst4;
		fConst8 = (fConst3 + -2.970442628930023) / fConst1 + 1.0;
		fConst9 = 2.0 * (1.0 - 1.0 / fConst2);
		fConst10 = 0.05258033106134372 / fConst5;
		fConst11 = 0.25 / fConst0;
		fConst12 = std::exp(-(666.6666666666666 / fConst0));
		fConst13 = 1e-06 * (1.0 - fConst12);
		fConst14 = 1.0 / fConst0;
		fConst15 = 5.654866776461628 * fConst0;
		fConst16 = 0.02629016553067186 / fConst5;
	}
	
	virtual void instanceResetUserInterface() {
		fHslider0 = static_cast<FAUSTFLOAT>(1.0);
		fHslider1 = static_cast<FAUSTFLOAT>(0.5);
		fEntry0 = static_cast<FAUSTFLOAT>(0.0);
		fCheckbox0 = static_cast<FAUSTFLOAT>(0.0);
		fHslider2 = static_cast<FAUSTFLOAT>(0.5);
		fEntry1 = static_cast<FAUSTFLOAT>(4.7e+04);
		fHslider3 = static_cast<FAUSTFLOAT>(0.5);
		fHslider4 = static_cast<FAUSTFLOAT>(0.5);
	}
	
	virtual void instanceClear() {
		for (int l0 = 0; l0 < 3; l0 = l0 + 1) {
			fRec0[l0] = 0.0;
		}
		IOTA0 = 0;
		for (int l1 = 0; l1 < 32; l1 = l1 + 1) {
			fVec0[l1] = 0.0;
		}
		for (int l2 = 0; l2 < 2; l2 = l2 + 1) {
			fRec18[l2] = 0.0;
		}
		for (int l3 = 0; l3 < 2; l3 = l3 + 1) {
			fRec17[l3] = 0.0;
		}
		for (int l4 = 0; l4 < 2; l4 = l4 + 1) {
			fRec19[l4] = 0.0;
		}
		for (int l5 = 0; l5 < 2; l5 = l5 + 1) {
			fRec13[l5] = 0.0;
		}
		for (int l6 = 0; l6 < 2; l6 = l6 + 1) {
			fRec14[l6] = 0.0;
		}
		for (int l7 = 0; l7 < 2; l7 = l7 + 1) {
			fRec9[l7] = 0.0;
		}
		for (int l8 = 0; l8 < 2; l8 = l8 + 1) {
			fRec10[l8] = 0.0;
		}
		for (int l9 = 0; l9 < 2; l9 = l9 + 1) {
			fRec5[l9] = 0.0;
		}
		for (int l10 = 0; l10 < 2; l10 = l10 + 1) {
			fRec6[l10] = 0.0;
		}
		for (int l11 = 0; l11 < 2; l11 = l11 + 1) {
			fRec1[l11] = 0.0;
		}
		for (int l12 = 0; l12 < 2; l12 = l12 + 1) {
			fRec2[l12] = 0.0;
		}
		for (int l13 = 0; l13 < 2; l13 = l13 + 1) {
			fRec20[l13] = 0.0;
		}
		for (int l14 = 0; l14 < 2; l14 = l14 + 1) {
			fRec21[l14] = 0.0;
		}
		for (int l15 = 0; l15 < 2; l15 = l15 + 1) {
			fRec24[l15] = 0.0;
		}
		for (int l16 = 0; l16 < 2; l16 = l16 + 1) {
			fRec25[l16] = 0.0;
		}
		for (int l17 = 0; l17 < 32; l17 = l17 + 1) {
			fVec1[l17] = 0.0;
		}
		for (int l18 = 0; l18 < 32; l18 = l18 + 1) {
			fVec2[l18] = 0.0;
		}
	}
	
	virtual void init(int sample_rate) {
		classInit(sample_rate);
		instanceInit(sample_rate);
	}
	
	virtual void instanceInit(int sample_rate) {
		instanceConstants(sample_rate);
		instanceResetUserInterface();
		instanceClear();
	}
	
	virtual ResonatorDSP* clone() {
		return new ResonatorDSP(*this);
	}
	
	virtual int getSampleRate() {
		return fSampleRate;
	}
	
	virtual void buildUserInterface(UI* ui_interface) {
		ui_interface->openVerticalBox("resonator");
		ui_interface->addHorizontalSlider("blend", &fHslider0, FAUSTFLOAT(1.0), FAUSTFLOAT(0.0), FAUSTFLOAT(1.0), FAUSTFLOAT(0.001));
		ui_interface->addCheckButton("bypass_vactrol", &fCheckbox0);
		ui_interface->addNumEntry("color", &fEntry0, FAUSTFLOAT(0.0), FAUSTFLOAT(0.0), FAUSTFLOAT(4.0), FAUSTFLOAT(1.0));
		ui_interface->addHorizontalSlider("cv", &fHslider2, FAUSTFLOAT(0.5), FAUSTFLOAT(0.0), FAUSTFLOAT(1.0), FAUSTFLOAT(0.001));
		ui_interface->addHorizontalSlider("peak1", &fHslider1, FAUSTFLOAT(0.5), FAUSTFLOAT(0.0), FAUSTFLOAT(1.0), FAUSTFLOAT(0.001));
		ui_interface->addHorizontalSlider("peak2", &fHslider3, FAUSTFLOAT(0.5), FAUSTFLOAT(0.0), FAUSTFLOAT(1.0), FAUSTFLOAT(0.001));
		ui_interface->addHorizontalSlider("peak3", &fHslider4, FAUSTFLOAT(0.5), FAUSTFLOAT(0.0), FAUSTFLOAT(1.0), FAUSTFLOAT(0.001));
		ui_interface->addNumEntry("rldr", &fEntry1, FAUSTFLOAT(4.7e+04), FAUSTFLOAT(1e+03), FAUSTFLOAT(1e+06), FAUSTFLOAT(1.0));
		ui_interface->closeBox();
	}
	
	virtual void compute(int count, FAUSTFLOAT** RESTRICT inputs, FAUSTFLOAT** RESTRICT outputs) {
		FAUSTFLOAT* input0 = inputs[0];
		FAUSTFLOAT* output0 = outputs[0];
		double fSlow0 = static_cast<double>(fHslider0);
		double fSlow1 = static_cast<double>(fHslider1);
		double fSlow2 = 1.0 / std::max<double>(1e+04 * fSlow1, 1.0) + 0.00010370370370370373;
		double fSlow3 = std::pow(2.0, -(0.425 * (1e+01 / (fSlow2 * (1.0 / fSlow2 + 1e+04 * (1.0 - fSlow1))) + -3.970588235294118)));
		double fSlow4 = 1.0 / fSlow3;
		double fSlow5 = static_cast<double>(fEntry0);
		int iSlow6 = fSlow5 >= 3.0;
		int iSlow7 = fSlow5 >= 2.0;
		int iSlow8 = fSlow5 >= 1.0;
		int iSlow9 = fSlow5 >= 4.0;
		double fSlow10 = ((iSlow6) ? ((iSlow9) ? 3.3e-08 : 3.9e-08) : ((iSlow7) ? 5.6e-08 : ((iSlow8) ? 6.8e-08 : 8.2e-08)));
		double fSlow11 = ((iSlow6) ? ((iSlow9) ? 3.3e-10 : 3.9e-10) : ((iSlow7) ? 5.6e-10 : ((iSlow8) ? 6.8e-10 : 8.2e-10)));
		double fSlow12 = std::sqrt(fSlow10 * fSlow11);
		int iSlow13 = static_cast<int>(static_cast<double>(fCheckbox0));
		double fSlow14 = fConst13 * (1.0 - 1.0 / std::pow(0.001, static_cast<double>(fHslider2)));
		double fSlow15 = static_cast<double>(fEntry1);
		double fSlow16 = 52.58033106134372 / fSlow3;
		double fSlow17 = 2.0 / fSlow3;
		double fSlow18 = 2.0 / fSlow10;
		double fSlow19 = 26.29016553067186 / fSlow3;
		double fSlow20 = static_cast<double>(fHslider3);
		double fSlow21 = 1.0 / std::max<double>(1e+04 * fSlow20, 1.0) + 0.00010370370370370373;
		double fSlow22 = std::pow(2.0, -1.0 - 0.425 * (1e+01 / (fSlow21 * (1.0 / fSlow21 + 1e+04 * (1.0 - fSlow20))) + -3.970588235294118));
		double fSlow23 = 1.0 / fSlow22;
		double fSlow24 = 2.0 / fSlow22;
		double fSlow25 = 52.58033106134372 / fSlow22;
		double fSlow26 = 26.29016553067186 / fSlow22;
		double fSlow27 = static_cast<double>(fHslider4);
		double fSlow28 = 1.0 / std::max<double>(1e+04 * fSlow27, 1.0) + 0.00010370370370370373;
		double fSlow29 = std::pow(2.0, -2.0 - 0.425 * (1e+01 / (fSlow28 * (1.0 / fSlow28 + 1e+04 * (1.0 - fSlow27))) + -3.970588235294118));
		double fSlow30 = 1.0 / fSlow29;
		double fSlow31 = 2.0 / fSlow29;
		double fSlow32 = 52.58033106134372 / fSlow29;
		double fSlow33 = 26.29016553067186 / fSlow29;
		for (int i0 = 0; i0 < count; i0 = i0 + 1) {
			fRec0[0] = static_cast<double>(input0[i0]) - fConst7 * (fConst8 * fRec0[2] + fConst9 * fRec0[1]);
			double fTemp0 = fRec0[2] + (fRec0[0] - 2.0 * fRec0[1]);
			fVec0[IOTA0 & 31] = fTemp0;
			double fTemp1 = fVec0[(IOTA0 - 31) & 31];
			double fTemp2 = fConst6 * fTemp1;
			fRec18[0] = fConst12 * fRec18[1] - fSlow14;
			double fTemp3 = 0.7 * fRec18[0];
			fRec17[0] = fRec17[1] + (fTemp3 - fRec17[1]) * (1.0 - std::exp(-(fConst14 / ((fTemp3 > fRec17[1]) ? 0.0035 : 1.6e-07 / (fRec17[1] + 7e-07)))));
			double fTemp4 = 0.3 * fRec18[0];
			fRec19[0] = fRec19[1] + (fTemp4 - fRec19[1]) * (1.0 - std::exp(-(fConst14 / ((fTemp4 > fRec19[1]) ? 0.0035 : 5e-07 / (fRec19[1] + 3e-07)))));
			double fTemp5 = ((iSlow13) ? fSlow15 : 1.0 / (fRec17[0] + fRec19[0] + 1e-06));
			double fTemp6 = fSlow12 * fTemp5;
			double fTemp7 = std::min<double>(fSlow4 / fTemp6, fConst15);
			double fTemp8 = std::tan(fConst11 * fTemp7);
			double fTemp9 = 1.0 / ResonatorDSP_faustpower2_f(fTemp8);
			double fTemp10 = 1.0 - fTemp9;
			double fTemp11 = fVec0[(IOTA0 - 15) & 31];
			double fTemp12 = fSlow16 / fTemp5 + 1.0;
			double fTemp13 = 1.0 / fTemp12 - fTemp9;
			double fTemp14 = 5.7331607250436385e-06 * fVec0[(IOTA0 - 1) & 31] - 1.262729840928248e-06 * fTemp0 - 1.5435485234355135e-05 * fVec0[(IOTA0 - 2) & 31] + 3.355184227501467e-05 * fVec0[(IOTA0 - 3) & 31] - 6.425715662070987e-05 * fVec0[(IOTA0 - 4) & 31] + 0.0001128325757038191 * fVec0[(IOTA0 - 5) & 31] - 0.00018584066193736996 * fVec0[(IOTA0 - 6) & 31] + 0.0002914867763540543 * fVec0[(IOTA0 - 7) & 31] - 0.0004404238885562187 * fVec0[(IOTA0 - 8) & 31] + 0.0006475573907550037 * fVec0[(IOTA0 - 9) & 31] - 0.0009361769702543772 * fVec0[(IOTA0 - 10) & 31] + 0.0013480378914712018 * fVec0[(IOTA0 - 11) & 31] - 0.001971203964977121 * fVec0[(IOTA0 - 12) & 31] + 0.0030351491656214354 * fVec0[(IOTA0 - 13) & 31] - 0.005386460673622843 * fVec0[(IOTA0 - 14) & 31] + 0.016671795493474285 * fTemp11 + 0.016671795493474285 * fVec0[(IOTA0 - 16) & 31] - 0.005386460673622843 * fVec0[(IOTA0 - 17) & 31] + 0.0030351491656214354 * fVec0[(IOTA0 - 18) & 31] - 0.001971203964977121 * fVec0[(IOTA0 - 19) & 31] + 0.0013480378914712018 * fVec0[(IOTA0 - 20) & 31] - 0.0009361769702543772 * fVec0[(IOTA0 - 21) & 31] + 0.0006475573907550037 * fVec0[(IOTA0 - 22) & 31] - 0.0004404238885562187 * fVec0[(IOTA0 - 23) & 31] + 0.0002914867763540543 * fVec0[(IOTA0 - 24) & 31] - 0.00018584066193736996 * fVec0[(IOTA0 - 25) & 31] + 0.0001128325757038191 * fVec0[(IOTA0 - 26) & 31] - 6.425715662070987e-05 * fVec0[(IOTA0 - 27) & 31] + 3.355184227501467e-05 * fVec0[(IOTA0 - 28) & 31] - 1.5435485234355135e-05 * fVec0[(IOTA0 - 29) & 31] + 5.7331607250436385e-06 * fVec0[(IOTA0 - 30) & 31] - 1.262729840928248e-06 * fTemp1;
			double fTemp15 = fSlow10 * fTemp5;
			double fTemp16 = fTemp15 * fTemp7;
			double fTemp17 = fSlow17 / (fTemp16 * fTemp8);
			double fTemp18 = fTemp9 + fTemp17 + 1.0;
			double fTemp19 = fSlow11 * fTemp5;
			double fTemp20 = fSlow4 * ((fSlow18 + fSlow19 / fTemp19) / (fTemp5 * fTemp7 * fTemp8));
			double fTemp21 = fTemp9 + (fTemp20 + 1.0) / fTemp12;
			double fTemp22 = fConst6 * (fTemp18 * fTemp14 / (fTemp12 * fTemp21)) + fRec13[1];
			double fTemp23 = fRec14[1] + (2.0 * (fConst6 * (fTemp10 * fTemp14 / fTemp12) - fTemp13 * fTemp22) + fConst16 * (fTemp11 * fTemp18 / fTemp12)) / fTemp21;
			double fTemp24 = fTemp9 + (1.0 - fTemp17);
			double fTemp25 = fTemp9 + (1.0 - fTemp20) / fTemp12;
			fRec13[0] = (fConst10 * (fTemp10 * fTemp11 / fTemp12) - 2.0 * fTemp13 * fTemp23 + (fConst6 * (fTemp14 * fTemp24 / fTemp12) - fTemp22 * fTemp25)) / fTemp21;
			fRec14[0] = (fConst16 * (fTemp11 * fTemp24 / fTemp12) - fTemp23 * fTemp25) / fTemp21;
			double fRec15 = fTemp22;
			double fRec16 = fTemp23;
			double fTemp26 = std::min<double>(fSlow23 / fTemp6, fConst15);
			double fTemp27 = std::tan(fConst11 * fTemp26);
			double fTemp28 = 1.0 / ResonatorDSP_faustpower2_f(fTemp27);
			double fTemp29 = fTemp15 * fTemp26;
			double fTemp30 = fSlow24 / (fTemp29 * fTemp27);
			double fTemp31 = fTemp28 + (1.0 - fTemp30);
			double fTemp32 = fSlow25 / fTemp5 + 1.0;
			double fTemp33 = fTemp28 + fTemp30 + 1.0;
			double fTemp34 = fSlow23 * ((fSlow18 + fSlow26 / fTemp19) / (fTemp5 * fTemp26 * fTemp27));
			double fTemp35 = fTemp28 + (fTemp34 + 1.0) / fTemp32;
			double fTemp36 = fRec15 * fTemp33 / (fTemp32 * fTemp35) + fRec9[1];
			double fTemp37 = fTemp28 + (1.0 - fTemp34) / fTemp32;
			double fTemp38 = 1.0 - fTemp28;
			double fTemp39 = 1.0 / fTemp32 - fTemp28;
			double fTemp40 = fRec10[1] + (fRec16 * fTemp33 / fTemp32 + 2.0 * (fRec15 * fTemp38 / fTemp32 - fTemp39 * fTemp36)) / fTemp35;
			fRec9[0] = (fRec15 * fTemp31 / fTemp32 - fTemp36 * fTemp37 + 2.0 * (fRec16 * fTemp38 / fTemp32 - fTemp39 * fTemp40)) / fTemp35;
			fRec10[0] = (fRec16 * fTemp31 / fTemp32 - fTemp40 * fTemp37) / fTemp35;
			double fRec11 = fTemp36;
			double fRec12 = fTemp40;
			double fTemp41 = std::min<double>(fSlow30 / fTemp6, fConst15);
			double fTemp42 = std::tan(fConst11 * fTemp41);
			double fTemp43 = 1.0 / ResonatorDSP_faustpower2_f(fTemp42);
			double fTemp44 = fTemp15 * fTemp41;
			double fTemp45 = fSlow31 / (fTemp44 * fTemp42);
			double fTemp46 = fTemp43 + (1.0 - fTemp45);
			double fTemp47 = fSlow32 / fTemp5 + 1.0;
			double fTemp48 = fTemp43 + fTemp45 + 1.0;
			double fTemp49 = fSlow30 * ((fSlow18 + fSlow33 / fTemp19) / (fTemp5 * fTemp41 * fTemp42));
			double fTemp50 = fTemp43 + (fTemp49 + 1.0) / fTemp47;
			double fTemp51 = fRec11 * fTemp48 / (fTemp47 * fTemp50) + fRec5[1];
			double fTemp52 = fTemp43 + (1.0 - fTemp49) / fTemp47;
			double fTemp53 = 1.0 - fTemp43;
			double fTemp54 = 1.0 / fTemp47 - fTemp43;
			double fTemp55 = fRec6[1] + (fRec12 * fTemp48 / fTemp47 + 2.0 * (fRec11 * fTemp53 / fTemp47 - fTemp54 * fTemp51)) / fTemp50;
			fRec5[0] = (fRec11 * fTemp46 / fTemp47 - fTemp51 * fTemp52 + 2.0 * (fRec12 * fTemp53 / fTemp47 - fTemp54 * fTemp55)) / fTemp50;
			fRec6[0] = (fRec12 * fTemp46 / fTemp47 - fTemp55 * fTemp52) / fTemp50;
			double fRec7 = fTemp51;
			double fRec8 = fTemp55;
			double fTemp56 = fTemp19 * fTemp7 * fTemp8;
			double fTemp57 = 1.0 / fTemp8;
			double fTemp58 = fSlow17 / fTemp16;
			double fTemp59 = (fTemp57 + fTemp58) / fTemp8 + 1.0;
			double fTemp60 = fRec1[1] - fSlow4 * (fRec7 / (fTemp56 * fTemp59));
			double fTemp61 = (fTemp57 - fTemp58) / fTemp8 + 1.0;
			double fTemp62 = fSlow4 * (fRec8 / fTemp56);
			double fTemp63 = fRec2[1] - (fTemp62 + 2.0 * fTemp10 * fTemp60) / fTemp59;
			fRec1[0] = (fSlow4 * (fRec7 / fTemp56) - fTemp60 * fTemp61 - 2.0 * fTemp10 * fTemp63) / fTemp59;
			fRec2[0] = (fTemp62 - fTemp63 * fTemp61) / fTemp59;
			double fRec3 = fTemp60;
			double fRec4 = fTemp63;
			double fTemp64 = fTemp19 * fTemp26 * fTemp27;
			double fTemp65 = 1.0 / fTemp27;
			double fTemp66 = fSlow24 / fTemp29;
			double fTemp67 = (fTemp65 + fTemp66) / fTemp27 + 1.0;
			double fTemp68 = fRec20[1] - fSlow23 * (fRec7 / (fTemp64 * fTemp67));
			double fTemp69 = (fTemp65 - fTemp66) / fTemp27 + 1.0;
			double fTemp70 = fSlow23 * (fRec8 / fTemp64);
			double fTemp71 = fRec21[1] - (fTemp70 + 2.0 * fTemp38 * fTemp68) / fTemp67;
			fRec20[0] = (fSlow23 * (fRec7 / fTemp64) - fTemp68 * fTemp69 - 2.0 * fTemp38 * fTemp71) / fTemp67;
			fRec21[0] = (fTemp70 - fTemp71 * fTemp69) / fTemp67;
			double fRec22 = fTemp68;
			double fRec23 = fTemp71;
			double fTemp72 = fTemp19 * fTemp41 * fTemp42;
			double fTemp73 = 1.0 / fTemp42;
			double fTemp74 = fSlow31 / fTemp44;
			double fTemp75 = (fTemp73 + fTemp74) / fTemp42 + 1.0;
			double fTemp76 = fRec24[1] - fSlow30 * (fRec7 / (fTemp72 * fTemp75));
			double fTemp77 = (fTemp73 - fTemp74) / fTemp42 + 1.0;
			double fTemp78 = fSlow30 * (fRec8 / fTemp72);
			double fTemp79 = fRec25[1] - (fTemp78 + 2.0 * fTemp53 * fTemp76) / fTemp75;
			fRec24[0] = (fSlow30 * (fRec7 / fTemp72) - fTemp76 * fTemp77 - 2.0 * fTemp53 * fTemp79) / fTemp75;
			fRec25[0] = (fTemp78 - fTemp79 * fTemp77) / fTemp75;
			double fRec26 = fTemp76;
			double fRec27 = fTemp79;
			fVec1[IOTA0 & 31] = fRec4 + fRec23 + fRec27;
			double fTemp80 = fRec3 + fRec22 + fRec26;
			fVec2[IOTA0 & 31] = fTemp80;
			output0[i0] = static_cast<FAUSTFLOAT>(fTemp2 - fSlow0 * (fTemp2 + 0.5 * (fVec1[(IOTA0 - 16) & 31] + (0.00021807244683777098 * (fVec2[(IOTA0 - 1) & 31] + fVec2[(IOTA0 - 30) & 31]) + 0.001276212667275558 * (fVec2[(IOTA0 - 3) & 31] + fVec2[(IOTA0 - 28) & 31]) + 0.004291816861030452 * (fVec2[(IOTA0 - 5) & 31] + fVec2[(IOTA0 - 26) & 31]) + 0.011087293307985696 * (fVec2[(IOTA0 - 7) & 31] + fVec2[(IOTA0 - 24) & 31]) + 0.024631164455755136 * (fVec2[(IOTA0 - 9) & 31] + fVec2[(IOTA0 - 22) & 31]) + 0.05127536720521941 * (fVec2[(IOTA0 - 11) & 31] + fVec2[(IOTA0 - 20) & 31]) + 0.6341457026591885 * (fVec2[(IOTA0 - 15) & 31] + fVec2[(IOTA0 - 16) & 31]) + 0.11544808122567458 * (fVec2[(IOTA0 - 13) & 31] + fVec2[(IOTA0 - 18) & 31]) - (0.0005871201235438045 * (fVec2[(IOTA0 - 2) & 31] + fVec2[(IOTA0 - 29) & 31]) + 0.0024441518462766307 * (fVec2[(IOTA0 - 4) & 31] + fVec2[(IOTA0 - 27) & 31]) + 0.00706882814109922 * (fVec2[(IOTA0 - 6) & 31] + fVec2[(IOTA0 - 25) & 31]) + 0.01675241976100876 * (fVec2[(IOTA0 - 8) & 31] + fVec2[(IOTA0 - 23) & 31]) + 0.035609398090786865 * (fVec2[(IOTA0 - 10) & 31] + fVec2[(IOTA0 - 21) & 31]) + 0.20488500414113556 * (fVec2[(IOTA0 - 14) & 31] + fVec2[(IOTA0 - 17) & 31]) + 0.07497875822338901 * (fVec2[(IOTA0 - 12) & 31] + fVec2[(IOTA0 - 19) & 31]) + 4.8030501727159655e-05 * (fTemp80 + fVec2[(IOTA0 - 31) & 31]))))));
			fRec0[2] = fRec0[1];
			fRec0[1] = fRec0[0];
			IOTA0 = IOTA0 + 1;
			fRec18[1] = fRec18[0];
			fRec17[1] = fRec17[0];
			fRec19[1] = fRec19[0];
			fRec13[1] = fRec13[0];
			fRec14[1] = fRec14[0];
			fRec9[1] = fRec9[0];
			fRec10[1] = fRec10[0];
			fRec5[1] = fRec5[0];
			fRec6[1] = fRec6[0];
			fRec1[1] = fRec1[0];
			fRec2[1] = fRec2[0];
			fRec20[1] = fRec20[0];
			fRec21[1] = fRec21[0];
			fRec24[1] = fRec24[0];
			fRec25[1] = fRec25[0];
		}
	}

};

#endif
