#pragma once
#include "asterbook/types.hpp"
#include <map>
#include <set>
#include <unordered_map>
#include <tuple>
namespace asterbook {
struct Order {
  std::string id, owner, symbol;
  Side side;
  std::int64_t price, qty;
  std::uint64_t priority;
};
// The engine is single-writer. Price/time ordering is explicit and deterministic.
class Book {
public:
  std::map<std::string, Order> orders;
  using Key = std::tuple<std::int64_t, std::uint64_t, std::string>;
  std::map<std::string, std::set<Key>> bids, asks;
  std::set<std::string> used_ids;
  void add(const Order &o) {
    orders.emplace(o.id, o);
    (o.side == Side::Buy ? bids : asks)[o.symbol].emplace(o.side == Side::Buy ? -o.price : o.price,
                                                          o.priority, o.id);
  }
  void erase(const std::string &id) {
    auto o = orders.at(id);
    auto &levels = (o.side == Side::Buy ? bids : asks);
    auto it = levels.find(o.symbol);
    it->second.erase(Key{o.side == Side::Buy ? -o.price : o.price, o.priority, o.id});
    if (it->second.empty())
      levels.erase(it);
    orders.erase(id);
  }
  std::uint64_t priority = 0;
};
}
