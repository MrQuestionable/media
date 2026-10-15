cmake_minimum_required(VERSION 3.18)
project(native_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs)

execute_process(
    COMMAND python -m pybind11 --cmakedir
    OUTPUT_STRIP_TRAILING_WHITESPACE
    OUTPUT_VARIABLE pybind11_DIR
)
list(APPEND CMAKE_PREFIX_PATH "${pybind11_DIR}")
find_package(pybind11 REQUIRED)

pybind11_add_module(native_engine
    cpp_core/src/processor.cpp
    cpp_core/src/bindings.cpp
)

target_include_directories(native_engine PRIVATE
    ${OpenCV_INCLUDE_DIRS}
    cpp_core/include
)

target_link_libraries(native_engine PRIVATE
    ${OpenCV_LIBS}
)
set_target_properties(native_engine PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/python_service/app"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/python_service/app"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/python_service/app"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/python_service/app"
)