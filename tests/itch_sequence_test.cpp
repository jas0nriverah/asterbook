#include "asterbook/itch.hpp"
#include <cassert>
#include <cstdint>
#include <stdexcept>
#include <vector>
int main() {
  asterbook::itch::Reconstructor b;
  std::vector<uint8_t> p(36);
  p[0] = 'A';
  p[2] = 3;
  p[18] = 1;
  p[19] = 'B';
  p[23] = 10;
  p[24] = 'A';
  for (int i = 25; i < 32; ++i)
    p[i] = ' ';
  p[35] = 1;
  asterbook::itch::Reconstructor limited(1, 1);
  limited.apply(p);
  p[18] = 2;
  try {
    limited.apply(p);
    assert(false);
  } catch (const std::invalid_argument &) {
  }
  assert(limited.orders().size() == 1);
  p[18] = 1;
  b.apply_sequenced(50, p);
  assert(b.orders().size() == 1);
  try {
    b.apply_sequenced(52, p);
    assert(false);
  } catch (const std::invalid_argument &) {
  }
  try {
    b.apply_sequenced(51, p);
    assert(false);
  } catch (const std::invalid_argument &) {
  }
  assert(b.orders().size() == 1);
  b.reset();
  b.apply_sequenced(1, p);
  assert(b.orders().size() == 1);
}
