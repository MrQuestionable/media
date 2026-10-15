import os
import sys

opencv_bin_dir = r"C:\opt\opencv\build\x64\vc16\bin"

if os.path.exists(opencv_bin_dir):
    os.add_dll_directory(opencv_bin_dir)
else:
    print(f"Warning: Directory {opencv_bin_dir} not found!")

import native_engine
import numpy as np
import cv2

img = np.zeros((300, 300, 3), dtype=np.uint8)
cv2.rectangle(img, (50, 50), (250, 250), (255, 255, 255), -1)

edges = native_engine.apply_edge_detection(img)
print("Execution succeeded! Output shape:", edges.shape)