#include "processor.hpp"
#include <opencv2/opencv.hpp>
#include <stdexcept>
#include <cstring>

py::array_t<uint8_t> apply_edge_detection(py::array_t<uint8_t>& input_image) {
    py::buffer_info buf = input_image.request();

    if (buf.ndim != 3 || buf.shape[2] != 3) {
        throw std::runtime_error("Input must be a 3-channel image (H, W, 3).");
    }

    int rows = static_cast<int>(buf.shape[0]);
    int cols = static_cast<int>(buf.shape[1]);

    cv::Mat img(rows, cols, CV_8UC3, static_cast<uint8_t*>(buf.ptr));

    cv::Mat gray, grad_x, grad_y, abs_grad_x, abs_grad_y, edges;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);

    cv::Sobel(gray, grad_x, CV_16S, 1, 0, 3);
    cv::Sobel(gray, grad_y, CV_16S, 0, 1, 3);
    cv::convertScaleAbs(grad_x, abs_grad_x);
    cv::convertScaleAbs(grad_y, abs_grad_y);
    cv::addWeighted(abs_grad_x, 0.5, abs_grad_y, 0.5, 0, edges);

    py::array_t<uint8_t> result({ rows, cols }, { cols * sizeof(uint8_t), sizeof(uint8_t) });
    py::buffer_info res_buf = result.request();
    std::memcpy(res_buf.ptr, edges.data, rows * cols);

    return result;
}