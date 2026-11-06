#include "asterbook/itch.hpp"
#include <cstddef>
#include <cstdint>
#include <span>
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  asterbook::itch::Reconstructor b;
  while (size >= 2) {
    size_t n = (size_t(data[0]) << 8) | data[1];
    data += 2;
    size -= 2;
    if (n > size)
      break;
    try {
      b.apply(std::span<const uint8_t>(data, n));
    } catch (...) {
    }
    data += n;
    size -= n;
  }
  return 0;
}
