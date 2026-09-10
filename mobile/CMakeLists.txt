cmake_minimum_required(VERSION 3.24)

project(MetriqVisualizerMobile VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

find_package(
    Qt6 6.8 REQUIRED
    COMPONENTS Core Gui Qml Quick QuickControls2 Multimedia ShaderTools
)

qt_standard_project_setup(REQUIRES 6.8)

add_library(metriq_mobile_core STATIC
    src/AudioFeatureEngine.cpp
    src/AudioFeatureEngine.h
    src/CpuVisualizerItem.cpp
    src/CpuVisualizerItem.h
    src/ProjectionMath.h
    src/RenderBackendInfo.cpp
    src/RenderBackendInfo.h
    src/StateCodec.cpp
    src/StateCodec.h
    src/VisualizerItem.cpp
    src/VisualizerItem.h
    src/VisualizerModel.cpp
    src/VisualizerModel.h
)

target_include_directories(metriq_mobile_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(
    metriq_mobile_core
    PUBLIC
        Qt6::Core
        Qt6::Gui
        Qt6::Qml
        Qt6::Quick
        Qt6::Multimedia
)
target_compile_definitions(
    metriq_mobile_core
    PRIVATE
        QT_NO_CAST_FROM_ASCII
        QT_NO_CAST_TO_ASCII
)

qt_add_executable(MetriqVisualizerMobile MANUAL_FINALIZATION src/main.cpp)
target_link_libraries(
    MetriqVisualizerMobile
    PRIVATE
        metriq_mobile_core
        Qt6::QuickControls2
)

qt_add_qml_module(
    MetriqVisualizerMobile
    URI Metriq.Mobile
    VERSION 1.0
    QML_FILES
        qml/Main.qml
        qml/components/InspectorSheet.qml
        qml/components/MetricChip.qml
)

qt_add_resources(
    MetriqVisualizerMobile mobile_resources
    PREFIX "/mobile"
    FILES
        resources/presets/Default.mvpreset
)

qt_add_shaders(
    MetriqVisualizerMobile metriq_mobile_shaders
    PREFIX "/mobile/shaders"
    BASE shaders
    FILES
        shaders/visualizer.vert
        shaders/visualizer.frag
)

set_target_properties(
    MetriqVisualizerMobile
    PROPERTIES
        WIN32_EXECUTABLE TRUE
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_BUNDLE_NAME "Metriq Visualizer"
        MACOSX_BUNDLE_GUI_IDENTIFIER "org.metriq.visualizer"
        MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
        MACOSX_BUNDLE_BUNDLE_VERSION "1"
)

if(ANDROID)
    set_target_properties(
        MetriqVisualizerMobile
        PROPERTIES
            QT_ANDROID_PACKAGE_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/platform/android"
            QT_ANDROID_PACKAGE_NAME "org.metriq.visualizer"
            QT_ANDROID_VERSION_NAME "${PROJECT_VERSION}"
            QT_ANDROID_VERSION_CODE 1
    )
endif()

if(IOS)
    configure_file(
        "${CMAKE_CURRENT_SOURCE_DIR}/platform/ios/Info.plist.in"
        "${CMAKE_CURRENT_BINARY_DIR}/Info.plist"
        @ONLY
    )
    set_target_properties(
        MetriqVisualizerMobile
        PROPERTIES
            MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_BINARY_DIR}/Info.plist"
            XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "org.metriq.visualizer"
            XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1,2"
    )
endif()

include(CTest)
if(BUILD_TESTING)
    find_package(Qt6 6.8 REQUIRED COMPONENTS Test)
    qt_add_executable(
        metriq_mobile_tests
        tests/tst_mobile_core.cpp
    )
    target_link_libraries(
        metriq_mobile_tests
        PRIVATE
            metriq_mobile_core
            Qt6::Test
            Qt6::Quick
    )
    add_test(NAME metriq_mobile_core COMMAND metriq_mobile_tests)
    set_tests_properties(
        metriq_mobile_core
        PROPERTIES
            ENVIRONMENT "QT_QPA_PLATFORM=offscreen;QSG_RHI_BACKEND=software"
    )
endif()

qt_finalize_executable(MetriqVisualizerMobile)
