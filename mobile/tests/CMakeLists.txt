qt_add_executable(metriq_mobile_tests
    tst_mobile.cpp
)

target_link_libraries(metriq_mobile_tests PRIVATE
    metriq_mobile_core
    metriq_mobile_render
    Qt6::Core
    Qt6::Gui
    Qt6::Test
)

add_test(NAME metriq_mobile_tests COMMAND metriq_mobile_tests)
set_tests_properties(metriq_mobile_tests PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen;QSG_RHI_BACKEND=software"
)
