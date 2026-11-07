#pragma once
#include "asterbook/types.hpp"
#include <map>
#include <memory>
#include <memory_resource>
#include <set>
#include <unordered_map>
#include <tuple>
namespace asterbook {
struct Order {
  std::string id, owner, symbol;
  Side side{Side::Buy};
  std::int64_t price{}, qty{};
  std::uint64_t priority{};
};
// The engine is single-writer. Price/time ordering is explicit and deterministic.
class Book {
public:
  explicit Book(bool pooled = false)
      : resource_(pooled ? std::shared_ptr<std::pmr::memory_resource>(
                               std::make_shared<std::pmr::unsynchronized_pool_resource>())
                         : std::shared_ptr<std::pmr::memory_resource>(
                               std::pmr::new_delete_resource(), [](auto *) {})),
        orders(resource_.get()), bids(resource_.get()), asks(resource_.get()),
        used_ids(resource_.get()) {}
  Book(Book &&other) noexcept
      : resource_(other.resource_), orders(std::move(other.orders)), bids(std::move(other.bids)),
        asks(std::move(other.asks)), used_ids(std::move(other.used_ids)), priority(other.priority) {
  }
  Book &operator=(Book &&) = delete;
  Book(const Book &) = delete;
  Book &operator=(const Book &) = delete;

private:
  std::shared_ptr<std::pmr::memory_resource> resource_;

public:
  std::pmr::map<std::string, Order> orders;
  using Key = std::tuple<std::int64_t, std::uint64_t, std::string>;
  std::pmr::map<std::string, std::pmr::set<Key>> bids, asks;
  std::pmr::set<std::string> used_ids;
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
  bool validate() const {
    std::size_t indexed = 0;
    std::set<std::uint64_t> priorities;
    for (const auto &[id, o] : orders) {
      if (id != o.id || o.qty <= 0 || o.price <= 0 || o.priority == 0 || o.priority > priority ||
          !used_ids.count(id) || !priorities.insert(o.priority).second)
        return false;
      const auto &side = o.side == Side::Buy ? bids : asks;
      auto level = side.find(o.symbol);
      if (level == side.end() ||
          !level->second.count(Key{o.side == Side::Buy ? -o.price : o.price, o.priority, id}))
        return false;
    }
    for (const auto *side : {&bids, &asks})
      for (const auto &[symbol, entries] : *side) {
        if (entries.empty())
          return false;
        indexed += entries.size();
      }
    if (indexed != orders.size())
      return false;
    for (const auto &[symbol, entries] : bids) {
      auto ask = asks.find(symbol);
      if (ask != asks.end() && -std::get<0>(*entries.begin()) >= std::get<0>(*ask->second.begin()))
        return false;
    }
    return true;
  }
  std::uint64_t priority = 0;
};
} // namespace asterbook
