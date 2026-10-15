#include <pybind11/pybind11.h>
#include "processor.hpp"

namespace py = pybind11;

PYBIND11_MODULE(native_engine, m) {
    m.doc() = "Native C++ OpenCV processing engine";
    m.def("apply_edge_detection", &apply_edge_detection, "Runs zero-copy Sobel edge detection on a NumPy array");
}