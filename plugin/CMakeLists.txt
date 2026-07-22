# PS3100 Resonator plugin (plan Phase 4): faust-generated DSP wrapped in a
# plain JUCE CMake project. JUCE is fetched automatically; the Faust compiler
# regenerates the DSP source from dsp/resonator.dsp when available, otherwise
# the committed copy in generated/ is used.
cmake_minimum_required(VERSION 3.24)
project(PS3100Resonator VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
FetchContent_Declare(
    JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 8.0.6
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(JUCE)

set(FAUST_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../dsp/resonator.dsp)
set(GENERATED ${CMAKE_CURRENT_BINARY_DIR}/resonator_faust.h)
find_program(FAUST_BIN faust)
if(FAUST_BIN)
    add_custom_command(
        OUTPUT ${GENERATED}
        COMMAND ${FAUST_BIN} -double -lang cpp -cn ResonatorDSP -o ${GENERATED} ${FAUST_SRC}
        DEPENDS ${FAUST_SRC} ${CMAKE_CURRENT_SOURCE_DIR}/../dsp/vactrol.dsp
        COMMENT "Regenerating Faust DSP from ${FAUST_SRC}"
    )
    set(GEN_INCLUDE_DIR ${CMAKE_CURRENT_BINARY_DIR})
else()
    message(WARNING "faust not found; using committed generated/resonator_faust.h")
    set(GENERATED ${CMAKE_CURRENT_SOURCE_DIR}/generated/resonator_faust.h)
    set(GEN_INCLUDE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/generated)
endif()

juce_add_plugin(PS3100Resonator
    COMPANY_NAME "ps3100-sim"
    PLUGIN_MANUFACTURER_CODE Ps31
    PLUGIN_CODE Klm6
    FORMATS VST3 AU Standalone
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE
    PRODUCT_NAME "PS3100 Resonator"
)

target_sources(PS3100Resonator PRIVATE PluginProcessor.cpp ${GENERATED})
set_source_files_properties(${GENERATED} PROPERTIES HEADER_FILE_ONLY TRUE)
target_include_directories(PS3100Resonator PRIVATE ${GEN_INCLUDE_DIR})
target_compile_definitions(PS3100Resonator PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
)
target_link_libraries(PS3100Resonator
    PRIVATE juce::juce_audio_utils
    PUBLIC juce::juce_recommended_config_flags
           juce::juce_recommended_warning_flags
)
