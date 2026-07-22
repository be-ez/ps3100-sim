#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

#include <array>
#include <atomic>
#include <memory>
#include <vector>

#include "FaustGlue.h"
#include "resonator_faust.h"

// APVTS parameter IDs. They intentionally equal the Faust widget labels from
// the parameter interface contract so the
// JUCE<->Faust mapping stays greppable. `bypass_vactrol`/`rldr` are test
// hooks and deliberately NOT exposed as plugin parameters.
namespace ParamIDs {
inline constexpr const char* cv = "cv";        // Sweep (RES MOD equivalent)
inline constexpr const char* peak1 = "peak1";  // panel PEAK CONT 1 (lowest band)
inline constexpr const char* peak2 = "peak2";
inline constexpr const char* peak3 = "peak3";
inline constexpr const char* blend = "blend";  // wet/dry pot k (0 = dry)
inline constexpr const char* color = "color";  // cap variant
}  // namespace ParamIDs

class ResonatorProcessor : public juce::AudioProcessor {
public:
    ResonatorProcessor();

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override {}
    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;
    void processBlock(juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    // Generic editor for now; the grouped/ordered parameter tree (see
    // makeLayout) is the layout a custom editor will bind to via `state()`.
    juce::AudioProcessorEditor* createEditor() override {
        return new juce::GenericAudioProcessorEditor(*this);
    }
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "PS3100 Resonator"; }
    bool acceptsMidi() const override { return false; }
    bool producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.5; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram(int) override {}
    const juce::String getProgramName(int) override { return {}; }
    void changeProgramName(int, const juce::String&) override {}

    void getStateInformation(juce::MemoryBlock& dest) override;
    void setStateInformation(const void* data, int sizeInBytes) override;

    // Hook for the future custom editor (attachments, undo, etc.).
    juce::AudioProcessorValueTreeState& state() { return apvts; }

private:
    static juce::AudioProcessorValueTreeState::ParameterLayout makeLayout();

    // One Faust instance per channel. Zone pointers are cached after
    // buildUserInterface; entries stay nullptr when the compiled DSP does not
    // (yet) expose that label, and writes are skipped - this is what lets the
    // plugin build against today's generated header and tomorrow's.
    struct Voice {
        ResonatorDSP dsp;
        ParamMap params;
        FAUSTFLOAT* cv = nullptr;
        FAUSTFLOAT* blend = nullptr;
        std::array<FAUSTFLOAT*, 3> peak{{nullptr, nullptr, nullptr}};
        FAUSTFLOAT* color = nullptr;
    };

    // Control-rate handling: cv/blend ramp toward their targets and all Faust
    // zones are refreshed every kControlInterval samples. The vactrol model
    // supplies the musical lag on cv; this short linear ramp only suppresses
    // zipper noise from stepped host automation.
    static constexpr int kControlInterval = 32;
    static constexpr double kSmoothingSeconds = 0.02;

    juce::AudioProcessorValueTreeState apvts;

    std::atomic<float>* cvParam = nullptr;
    std::atomic<float>* blendParam = nullptr;
    std::array<std::atomic<float>*, 3> peakParam{{nullptr, nullptr, nullptr}};
    std::atomic<float>* colorParam = nullptr;

    juce::SmoothedValue<float, juce::ValueSmoothingTypes::Linear> cvSmooth, blendSmooth;

    std::vector<std::unique_ptr<Voice>> voices;
    juce::AudioBuffer<double> scratch;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(ResonatorProcessor)
};
