#pragma once
#include <cstdint>
#include <map>
#include <optional>
#include <span>
#include <string>
#include <unordered_set>

namespace asterbook::itch {
struct Order {
  uint16_t locate{};
  char side{};
  uint32_t shares{};
  std::string symbol;
  uint32_t price{};
  std::optional<std::string> attribution;
};
// Validation errors preserve state. Discard the instance after allocation failure.
class Reconstructor {
public:
  explicit Reconstructor(size_t max_live = 1000000, size_t max_seen = 2000000)
      : max_live_(max_live), max_seen_(max_seen) {}
  void apply(std::span<const uint8_t> payload);
  // A gap or duplicate poisons this session until reset. First sequence is arbitrary.
  void apply_sequenced(uint64_t sequence, std::span<const uint8_t> payload);
  void reset();
  const std::map<uint64_t, Order> &orders() const { return orders_; }

private:
  size_t max_live_, max_seen_;
  std::map<uint64_t, Order> orders_;
  std::unordered_set<uint64_t> seen_;
  std::optional<uint64_t> next_;
  bool poisoned_ = false;
};
}
