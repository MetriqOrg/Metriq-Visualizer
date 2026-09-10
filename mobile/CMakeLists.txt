cmake_minimum_required(VERSION 3.21)

project(MetriqVisualizerMobile VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Qt6 6.8 REQUIRED COMPONENTS
    Core
    Gui
    Qml
    Quick
    QuickControls2
    Multimedia
    ShaderTools
)

qt_standard_project_setup(REQUIRES 6.8)

add_library(metriq_mobile_core STATIC
    src/audioanalysis.cpp
    src/audioanalysis.h
    src/audioengine.cpp
    src/audioengine.h
    src/scenecontroller.cpp
    src/scenecontroller.h
    src/scenepoint.h
)
target_include_directories(metriq_mobile_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(metriq_mobile_core PUBLIC
    Qt6::Core
    Qt6::Gui
    Qt6::Multimedia
)

add_library(metriq_mobile_render STATIC
    src/cpuvisualizeritem.cpp
    src/cpuvisualizeritem.h
    src/visualizergeometry.cpp
    src/visualizergeometry.h
    src/visualizermaterial.cpp
    src/visualizermaterial.h
    src/visualizergpuitem.cpp
    src/visualizergpuitem.h
)
target_include_directories(metriq_mobile_render PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(metriq_mobile_render PUBLIC
    metriq_mobile_core
    Qt6::Core
    Qt6::Gui
    Qt6::Quick
)

qt_add_executable(metriq-visualizer-mobile MANUAL_FINALIZATION
    src/main.cpp
)

target_link_libraries(metriq-visualizer-mobile PRIVATE
    metriq_mobile_core
    metriq_mobile_render
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
    Qt6::QuickControls2
    Qt6::Multimedia
)

set_source_files_properties(qml/Main.qml PROPERTIES QT_RESOURCE_ALIAS Main.qml)
set_source_files_properties(qml/components/GlassPanel.qml PROPERTIES QT_RESOURCE_ALIAS components/GlassPanel.qml)
set_source_files_properties(qml/components/InspectorSheet.qml PROPERTIES QT_RESOURCE_ALIAS components/InspectorSheet.qml)
set_source_files_properties(qml/components/MetricPill.qml PROPERTIES QT_RESOURCE_ALIAS components/MetricPill.qml)
set_source_files_properties(qml/components/TransportBar.qml PROPERTIES QT_RESOURCE_ALIAS components/TransportBar.qml)

qt_add_qml_module(metriq-visualizer-mobile
    URI Metriq.Visualizer.Mobile
    VERSION 1.0
    QML_FILES
        qml/Main.qml
        qml/components/GlassPanel.qml
        qml/components/InspectorSheet.qml
        qml/components/MetricPill.qml
        qml/components/TransportBar.qml
)

qt_add_shaders(metriq-visualizer-mobile "metriq_visualizer_mobile_shaders"
    PREFIX "/metriq/visualizer/mobile"
    FILES
        shaders/visualizer.vert
        shaders/visualizer.frag
)

set_target_properties(metriq-visualizer-mobile PROPERTIES
    MACOSX_BUNDLE TRUE
    MACOSX_BUNDLE_GUI_IDENTIFIER "org.metriq.visualizer"
    MACOSX_BUNDLE_BUNDLE_NAME "Metriq Visualizer"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
    MACOSX_BUNDLE_BUNDLE_VERSION "1"
    WIN32_EXECUTABLE TRUE
    QT_QMLCACHEGEN_ARGUMENTS "--only-bytecode"
)

if(ANDROID)
    set_target_properties(metriq-visualizer-mobile PROPERTIES
        QT_ANDROID_PACKAGE_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/platform/android"
        QT_ANDROID_PACKAGE_NAME "org.metriq.visualizer"
        QT_ANDROID_VERSION_NAME "${PROJECT_VERSION}"
        QT_ANDROID_VERSION_CODE 1
    )
endif()

if(APPLE AND CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set_target_properties(metriq-visualizer-mobile PROPERTIES
        MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/platform/ios/Info.plist.in"
        XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "org.metriq.visualizer"
        XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1,2"
    )
endif()

include(CTest)
if(BUILD_TESTING AND NOT ANDROID AND NOT CMAKE_SYSTEM_NAME STREQUAL "iOS")
    find_package(Qt6 6.8 REQUIRED COMPONENTS Test)
    add_subdirectory(tests)
endif()

qt_finalize_executable(metriq-visualizer-mobile)
