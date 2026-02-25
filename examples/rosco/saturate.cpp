// User-provided C++ implementation of ROSCO saturate function.
// This is what you provide to VIT — the translation itself.
// VIT handles everything else: interface, verification, integration.

#include <algorithm>

double saturate(double val, double minval, double maxval) {
    return std::min(std::max(val, minval), maxval);
}
