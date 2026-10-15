#pragma once
#include <pybind11/numpy.h>

namespace py = pybind11;

py::array_t<uint8_t> apply_edge_detection(py::array_t<uint8_t>& input_image);