#include "asterbook/itch.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>

namespace asterbook::itch {
namespace {
uint64_t number(std::span<const uint8_t> p, size_t off, size_t n) {
  uint64_t v = 0;
  for (size_t i = 0; i < n; ++i)
    v = (v << 8) | p[off + i];
  return v;
}
std::string ascii(std::span<const uint8_t> p, size_t off, size_t n) {
  std::string s;
  for (size_t i = 0; i < n; ++i) {
    if (p[off + i] < 32 || p[off + i] > 126)
      throw std::invalid_argument("non-ASCII text");
    s += static_cast<char>(p[off + i]);
  }
  return s;
}
void require(bool ok, const char *error) {
  if (!ok)
    throw std::invalid_argument(error);
}
}
void Reconstructor::apply(std::span<const uint8_t> p) {
  require(!p.empty(), "empty payload");
  size_t len = 0;
  switch (p[0]) {
  case 'A':
    len = 36;
    break;
  case 'F':
    len = 40;
    break;
  case 'E':
    len = 31;
    break;
  case 'C':
    len = 36;
    break;
  case 'X':
    len = 23;
    break;
  case 'D':
    len = 19;
    break;
  case 'U':
    len = 35;
    break;
  default:
    throw std::invalid_argument("unsupported type");
  }
  require(p.size() == len, "invalid length");
  auto locate = static_cast<uint16_t>(number(p, 1, 2));
  auto ref = number(p, 11, 8);
  if (p[0] == 'A' || p[0] == 'F') {
    auto symbol = ascii(p, 24, 8);
    while (!symbol.empty() && symbol.back() == ' ')
      symbol.pop_back();
    std::optional<std::string> attribution;
    if (p[0] == 'F')
      attribution = ascii(p, 36, 4);
    auto qty = static_cast<uint32_t>(number(p, 20, 4));
    auto price = static_cast<uint32_t>(number(p, 32, 4));
    require(!seen_.count(ref) && (p[19] == 'B' || p[19] == 'S') && qty && price && !symbol.empty(),
            "invalid or duplicate add");
    require(orders_.size() < max_live_ && seen_.size() < max_seen_, "order capacity exceeded");
    orders_.emplace(ref, Order{locate, static_cast<char>(p[19]), qty, std::move(symbol), price,
                               std::move(attribution)});
    seen_.insert(ref);
    return;
  }
  auto it = orders_.find(ref);
  require(it != orders_.end() && it->second.locate == locate, "unknown order or locate mismatch");
  if (p[0] == 'D') {
    orders_.erase(it);
    return;
  }
  if (p[0] == 'U') {
    auto next = number(p, 19, 8);
    auto qty = static_cast<uint32_t>(number(p, 27, 4));
    auto price = static_cast<uint32_t>(number(p, 31, 4));
    require(!seen_.count(next) && qty && price, "invalid or duplicate replacement");
    require(seen_.size() < max_seen_, "seen capacity exceeded");
    auto replacement = it->second;
    replacement.shares = qty;
    replacement.price = price;
    orders_.emplace(next, std::move(replacement));
    seen_.insert(next);
    orders_.erase(it);
    return;
  }
  auto qty = static_cast<uint32_t>(number(p, 19, 4));
  require(qty && qty <= it->second.shares, "invalid reduction");
  if (p[0] == 'C')
    require(p[31] == 'Y' || p[31] == 'N', "invalid printable flag");
  it->second.shares -= qty;
  if (!it->second.shares)
    orders_.erase(it);
}
void Reconstructor::apply_sequenced(uint64_t sequence, std::span<const uint8_t> p) {
  require(!poisoned_, "session requires reset");
  if ((next_ && sequence != *next_) || sequence == std::numeric_limits<uint64_t>::max()) {
    poisoned_ = true;
    throw std::invalid_argument("sequence gap, duplicate, or overflow");
  }
  try {
    apply(p);
  } catch (...) {
    poisoned_ = true;
    throw;
  }
  next_ = sequence + 1;
}
void Reconstructor::reset() {
  orders_.clear();
  seen_.clear();
  next_.reset();
  poisoned_ = false;
}
}
