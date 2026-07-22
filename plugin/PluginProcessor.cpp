#include "PluginProcessor.h"

// 0..1 parameters are displayed as percentages (panel-pot feel). Typed text
// is interpreted as percent to round-trip the display format.
static juce::AudioParameterFloatAttributes percentAttributes() {
    return juce::AudioParameterFloatAttributes()
        .withStringFromValueFunction(
            [](float v, int) { return juce::String(juce::roundToInt(v * 100.0f)) + " %"; })
        .withValueFromStringFunction([](const juce::String& s) {
            return juce::jlimit(0.0f, 1.0f, s.getFloatValue() / 100.0f);
        });
}

juce::AudioProcessorValueTreeState::ParameterLayout ResonatorProcessor::makeLayout() {
    using FloatParam = juce::AudioParameterFloat;
    const juce::NormalisableRange<float> unit(0.0f, 1.0f, 0.001f);

    // Grouped and ordered the way a custom editor will present them:
    // sweep first, the three panel peak pots, then output controls.
    auto sweep = std::make_unique<juce::AudioProcessorParameterGroup>(
        "sweep", "Sweep", "|",
        std::make_unique<FloatParam>(juce::ParameterID{ParamIDs::cv, 1}, "Sweep", unit, 0.5f,
                                     percentAttributes()));

    // PEAK CONT pots are per-band frequency offsets (~4 octaves of travel via
    // the KLM-62D CV matrix), not depth controls. 0.5 = factory-trim center
    // where the bands sit at the stock octave stagger.
    auto peaks = std::make_unique<juce::AudioProcessorParameterGroup>(
        "peaks", "Peaks", "|",
        std::make_unique<FloatParam>(juce::ParameterID{ParamIDs::peak1, 1}, "Peak 1 (Low)", unit,
                                     0.5f, percentAttributes()),
        std::make_unique<FloatParam>(juce::ParameterID{ParamIDs::peak2, 1}, "Peak 2 (Mid)", unit,
                                     0.5f, percentAttributes()),
        std::make_unique<FloatParam>(juce::ParameterID{ParamIDs::peak3, 1}, "Peak 3 (High)", unit,
                                     0.5f, percentAttributes()));

    auto output = std::make_unique<juce::AudioProcessorParameterGroup>(
        "output", "Output", "|",
        std::make_unique<FloatParam>(juce::ParameterID{ParamIDs::blend, 1}, "Blend", unit, 1.0f,
                                     percentAttributes()),
        std::make_unique<juce::AudioParameterChoice>(
            juce::ParameterID{ParamIDs::color, 1}, "Color",
            juce::StringArray{"Yellow", "Green", "Blue", "Gray", "White"}, 0));

    juce::AudioProcessorValueTreeState::ParameterLayout layout;
    layout.add(std::move(sweep));
    layout.add(std::move(peaks));
    layout.add(std::move(output));
    return layout;
}

ResonatorProcessor::ResonatorProcessor()
    : AudioProcessor(BusesProperties()
                         .withInput("Input", juce::AudioChannelSet::stereo(), true)
                         .withOutput("Output", juce::AudioChannelSet::stereo(), true)),
      apvts(*this, nullptr, "params", makeLayout()) {
    cvParam = apvts.getRawParameterValue(ParamIDs::cv);
    blendParam = apvts.getRawParameterValue(ParamIDs::blend);
    peakParam = {apvts.getRawParameterValue(ParamIDs::peak1),
                 apvts.getRawParameterValue(ParamIDs::peak2),
                 apvts.getRawParameterValue(ParamIDs::peak3)};
    colorParam = apvts.getRawParameterValue(ParamIDs::color);
}

bool ResonatorProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const {
    auto in = layouts.getMainInputChannelSet();
    auto out = layouts.getMainOutputChannelSet();
    return in == out &&
           (in == juce::AudioChannelSet::mono() || in == juce::AudioChannelSet::stereo());
}

void ResonatorProcessor::prepareToPlay(double sampleRate, int samplesPerBlock) {
    int nch = juce::jmax(1, getMainBusNumInputChannels());
    voices.clear();
    for (int c = 0; c < nch; c++) {
        auto v = std::make_unique<Voice>();
        v->dsp.buildUserInterface(&v->params);
        v->dsp.init((int)sampleRate);
        v->cv = v->params.find("cv");
        v->blend = v->params.find("blend");
        v->peak = {v->params.find("peak1"), v->params.find("peak2"), v->params.find("peak3")};
        v->color = v->params.find("color");
        voices.push_back(std::move(v));
    }
    scratch.setSize(2, samplesPerBlock);

    // wet-path group delay of the 2x-oversampling halfband pair; the DSP
    // delays its dry leg internally so blend stays time-aligned
    setLatencySamples(31);

    cvSmooth.reset(sampleRate, kSmoothingSeconds);
    blendSmooth.reset(sampleRate, kSmoothingSeconds);
    cvSmooth.setCurrentAndTargetValue(cvParam->load());
    blendSmooth.setCurrentAndTargetValue(blendParam->load());
}

void ResonatorProcessor::processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer&) {
    juce::ScopedNoDenormals noDenormals;
    const int numSamples = buffer.getNumSamples();
    const int nch = juce::jmin(buffer.getNumChannels(), (int)voices.size());

    cvSmooth.setTargetValue(cvParam->load());
    blendSmooth.setTargetValue(blendParam->load());
    const float p1 = peakParam[0]->load();
    const float p2 = peakParam[1]->load();
    const float p3 = peakParam[2]->load();
    const float color = colorParam->load();

    if (scratch.getNumSamples() < numSamples) scratch.setSize(2, numSamples, false, false, true);

    // Sub-block loop: advance the cv/blend ramps every kControlInterval
    // samples so stepped host automation can't produce zipper noise, while
    // keeping the Faust control rate (per-compute) semantics.
    for (int start = 0; start < numSamples; start += kControlInterval) {
        const int len = juce::jmin(kControlInterval, numSamples - start);
        const float cv = cvSmooth.skip(len);
        const float blend = blendSmooth.skip(len);

        for (int c = 0; c < nch; c++) {
            auto& v = *voices[(size_t)c];
            if (v.cv) *v.cv = cv;
            if (v.blend) *v.blend = blend;
            if (v.peak[0]) *v.peak[0] = p1;
            if (v.peak[1]) *v.peak[1] = p2;
            if (v.peak[2]) *v.peak[2] = p3;
            if (v.color) *v.color = color;

            double* in = scratch.getWritePointer(0);
            double* out = scratch.getWritePointer(1);
            const float* src = buffer.getReadPointer(c) + start;
            for (int i = 0; i < len; i++) in[i] = (double)src[i];

            double* ins[1] = {in};
            double* outs[1] = {out};
            v.dsp.compute(len, ins, outs);

            float* dst = buffer.getWritePointer(c) + start;
            for (int i = 0; i < len; i++) dst[i] = (float)out[i];
        }
    }
}

void ResonatorProcessor::getStateInformation(juce::MemoryBlock& dest) {
    if (auto xml = apvts.copyState().createXml()) copyXmlToBinary(*xml, dest);
}

void ResonatorProcessor::setStateInformation(const void* data, int sizeInBytes) {
    if (auto xml = getXmlFromBinary(data, sizeInBytes)) {
        apvts.replaceState(juce::ValueTree::fromXml(*xml));
        // Jump the ramps so restored state doesn't glide in from stale values.
        cvSmooth.setCurrentAndTargetValue(cvParam->load());
        blendSmooth.setCurrentAndTargetValue(blendParam->load());
    }
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter() { return new ResonatorProcessor(); }
