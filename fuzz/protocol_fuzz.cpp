#include "asterbook/protocol.hpp"
#include <cstddef>
#include <cstdint>
#include <string>
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  asterbook::Request r;
  std::string error;
  asterbook::parse_request(std::string_view(reinterpret_cast<const char *>(data), size), r, error);
  return 0;
}
