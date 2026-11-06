#include "asterbook/engine.hpp"
#include <algorithm>
#include <new>
#include <type_traits>
#include <sstream>
#include <stdexcept>
namespace asterbook {
namespace {
void field(std::string &s, const std::string &v) { s += std::to_string(v.size()) + ":" + v; }
std::string quote(const std::string &s) {
  std::string r = "\"";
  const char *hex = "0123456789abcdef";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') {
      r += '\\';
      r += char(c);
    } else if (c < 32) {
      r += "\\u00";
      r += hex[c >> 4];
      r += hex[c & 15];
    } else
      r += char(c);
  }
  return r + '"';
}
bool valid_text(const std::string &s) {
  return !s.empty() && s.size() <= 128 &&
         std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= 33 && c <= 126; });
}
} // namespace
std::string canonical_command(const Command &c) {
  std::string s;
  field(s, c.client);
  field(s, c.request);
  field(s, c.symbol);
  field(s, c.id);
  field(s, c.new_id);
  s += std::to_string((int)c.op) + "," + std::to_string((int)c.side) + "," +
       std::to_string((int)c.tif) + "," + std::to_string(c.price) + "," + std::to_string(c.qty);
  return s;
}
Engine::Engine(std::size_t live, std::size_t history)
    : Engine(EngineOptions{live, history, AllocatorMode::Baseline}) {}
Engine::Engine(EngineOptions options)
    : book_(options.allocator == AllocatorMode::Pooled), max_live_(options.max_live),
      max_history_(options.max_history), allocator_(options.allocator) {}
// Duplicate results are retained until capacity is reached; they are never evicted.
std::optional<Result> Engine::precheck(const Command &c) const {
  auto it = dedup_.find(std::make_pair(c.client, c.request));
  if (it != dedup_.end()) {
    if (it->second.first == canonical_command(c)) {
      auto r = it->second.second;
      r.duplicate = true;
      return r;
    }
    return Result{it->second.second.sequence, false, "REQUEST_CONFLICT", {}, false};
  }
  if (dedup_.size() >= max_history_)
    return Result{sequence_, false, "HISTORY_CAPACITY", {}, false};
  return std::nullopt;
}
Result Engine::execute(const Command &c) {
  if (auto result = precheck(c))
    return *result;
  auto key = std::make_pair(c.client, c.request);
  auto canonical = canonical_command(c);
  Result r;
  r.sequence = ++sequence_;
  auto reject = [&](const char *code) { r.code = code; };
  if (!valid_text(c.client) || !valid_text(c.request) || !valid_text(c.symbol) ||
      !valid_text(c.id) || (c.op != Op::New && c.op != Op::Cancel && c.op != Op::Replace) ||
      (c.side != Side::Buy && c.side != Side::Sell) || (c.tif != Tif::Gtc && c.tif != Tif::Ioc) ||
      (c.op == Op::Replace && c.tif != Tif::Gtc))
    reject("INVALID_COMMAND");
  else if (c.op != Op::New && (book_.orders.find(c.id) == book_.orders.end() ||
                               book_.orders.at(c.id).symbol != c.symbol))
    reject("UNKNOWN_ORDER");
  else if (c.op != Op::New && book_.orders.at(c.id).owner != c.client)
    reject("NOT_OWNER");
  else if (c.op != Op::Cancel && (c.price <= 0 || c.qty <= 0))
    reject("INVALID_PRICE_OR_QTY");
  else if (c.op == Op::Replace && !valid_text(c.new_id))
    reject("INVALID_COMMAND");
  else if (c.op != Op::Cancel && book_.used_ids.count(c.op == Op::New ? c.id : c.new_id))
    reject("DUPLICATE_ORDER_ID");
  else if (c.op != Op::Cancel && book_.used_ids.size() >= max_history_)
    reject("ID_CAPACITY");
  else {
    Order incoming;
    bool place = c.op != Op::Cancel;
    if (c.op == Op::New)
      incoming = {c.id, c.client, c.symbol, c.side, c.price, c.qty, ++book_.priority};
    if (c.op == Op::Replace) {
      auto old = book_.orders.at(c.id);
      incoming = {c.new_id, c.client, c.symbol, old.side, c.price, c.qty, ++book_.priority};
    }
    // A resting slot may be needed after matching; rejecting conservatively preserves atomicity.
    std::size_t remaining_live = book_.orders.size() - (c.op == Op::Replace ? 1 : 0);
    if (place && c.tif == Tif::Gtc && remaining_live >= max_live_) {
      if (c.op == Op::New || c.op == Op::Replace)
        --book_.priority;
      reject("LIVE_CAPACITY");
    } else {
      r.accepted = true;
      r.code = "OK";
      if (c.op != Op::New) {
        auto old = book_.orders.at(c.id);
        book_.erase(c.id);
        r.events.push_back({"CANCEL", c.symbol, c.id, "", old.price, old.qty});
      }
      if (place) {
        book_.used_ids.insert(incoming.id);
        while (incoming.qty > 0) {
          auto &opposite = incoming.side == Side::Buy ? book_.asks : book_.bids;
          auto level = opposite.find(incoming.symbol);
          if (level == opposite.end() || level->second.empty())
            break;
          const auto &key = *level->second.begin();
          auto best = book_.orders.find(std::get<2>(key));
          if (incoming.side == Side::Buy ? best->second.price > incoming.price
                                         : best->second.price < incoming.price)
            break;
          auto amount = std::min(incoming.qty, best->second.qty);
          r.events.push_back(
              {"TRADE", incoming.symbol, incoming.id, best->second.id, best->second.price, amount});
          incoming.qty -= amount;
          best->second.qty -= amount;
          if (best->second.qty == 0)
            book_.erase(best->first);
        }
        if (incoming.qty && c.tif == Tif::Gtc) {
          book_.add(incoming);
          r.events.push_back(
              {"REST", incoming.symbol, incoming.id, "", incoming.price, incoming.qty});
        } else if (incoming.qty)
          r.events.push_back(
              {"EXPIRE", incoming.symbol, incoming.id, "", incoming.price, incoming.qty});
      }
    }
  }
  dedup_.emplace(std::move(key), std::make_pair(std::move(canonical), r));
  return r;
}
std::string Engine::state() const {
  std::ostringstream s;
  s << "{\"sequence\":" << sequence_ << ",\"orders\":[";
  bool first = true;
  for (const auto &[id, o] : book_.orders) {
    if (!first)
      s << ',';
    first = false;
    s << "{\"id\":" << quote(id) << ",\"owner\":" << quote(o.owner)
      << ",\"symbol\":" << quote(o.symbol)
      << ",\"side\":" << quote(o.side == Side::Buy ? "BUY" : "SELL") << ",\"price\":" << o.price
      << ",\"qty\":" << o.qty << ",\"priority\":" << o.priority << '}';
  }
  s << "]}";
  return s.str();
}
namespace {
void number(std::string &s, std::uint64_t x) {
  for (int i = 0; i < 8; ++i)
    s += char(x >> (i * 8));
}
std::uint64_t number(const std::string &s, std::size_t &p) {
  if (s.size() - p < 8)
    throw std::runtime_error("invalid checkpoint");
  std::uint64_t x = 0;
  for (int i = 0; i < 8; ++i)
    x |= std::uint64_t((unsigned char)s[p++]) << (i * 8);
  return x;
}
void textfield(std::string &s, const std::string &x) {
  number(s, x.size());
  s += x;
}
std::string textfield(const std::string &s, std::size_t &p) {
  auto n = number(s, p);
  if (n > s.size() - p)
    throw std::runtime_error("invalid checkpoint field");
  auto x = s.substr(p, n);
  p += n;
  return x;
}
} // namespace
std::string Engine::checkpoint() const {
  std::string s;
  number(s, max_live_);
  number(s, max_history_);
  number(s, sequence_);
  number(s, book_.priority);
  number(s, book_.used_ids.size());
  for (const auto &id : book_.used_ids)
    textfield(s, id);
  number(s, book_.orders.size());
  for (const auto &[id, o] : book_.orders) {
    textfield(s, id);
    textfield(s, o.owner);
    textfield(s, o.symbol);
    number(s, (unsigned)o.side);
    number(s, o.price);
    number(s, o.qty);
    number(s, o.priority);
  }
  number(s, dedup_.size());
  for (const auto &[k, v] : dedup_) {
    textfield(s, k.first);
    textfield(s, k.second);
    textfield(s, v.first);
    const auto &r = v.second;
    number(s, r.sequence);
    number(s, r.accepted);
    textfield(s, r.code);
    number(s, r.events.size());
    for (const auto &e : r.events) {
      textfield(s, e.type);
      textfield(s, e.symbol);
      textfield(s, e.order_id);
      textfield(s, e.contra_id);
      number(s, e.price);
      number(s, e.qty);
    }
  }
  return s;
}
void Engine::restore(const std::string &s) {
  std::size_t p = 0;
  auto live = number(s, p), history = number(s, p);
  if (live != max_live_ || history != max_history_)
    throw std::runtime_error("checkpoint capacity mismatch");
  Engine next(EngineOptions{max_live_, max_history_, allocator_});
  next.sequence_ = number(s, p);
  next.book_.priority = number(s, p);
  if (next.book_.priority > next.sequence_)
    throw std::runtime_error("invalid checkpoint priority");
  auto n = number(s, p);
  if (n > max_history_)
    throw std::runtime_error("invalid checkpoint IDs");
  for (std::uint64_t i = 0; i < n; ++i) {
    auto id = textfield(s, p);
    if (!valid_text(id) || !next.book_.used_ids.insert(id).second)
      throw std::runtime_error("invalid checkpoint ID");
  }
  n = number(s, p);
  if (n > max_live_)
    throw std::runtime_error("invalid checkpoint orders");
  std::set<std::uint64_t> priorities;
  for (std::uint64_t i = 0; i < n; ++i) {
    Order o;
    o.id = textfield(s, p);
    o.owner = textfield(s, p);
    o.symbol = textfield(s, p);
    o.side = (Side)number(s, p);
    o.price = (std::int64_t)number(s, p);
    o.qty = (std::int64_t)number(s, p);
    o.priority = number(s, p);
    if (!next.book_.used_ids.count(o.id) || next.book_.orders.count(o.id) || o.price <= 0 ||
        o.qty <= 0 || o.priority == 0 || o.priority > next.book_.priority ||
        !priorities.insert(o.priority).second || !valid_text(o.id) || !valid_text(o.owner) ||
        !valid_text(o.symbol) || (o.side != Side::Buy && o.side != Side::Sell))
      throw std::runtime_error("invalid checkpoint order");
    next.book_.add(o);
  }
  n = number(s, p);
  if (n > max_history_ || n != next.sequence_)
    throw std::runtime_error("invalid checkpoint requests");
  std::set<std::uint64_t> sequences;
  for (std::uint64_t i = 0; i < n; ++i) {
    auto client = textfield(s, p), request = textfield(s, p), canonical = textfield(s, p);
    Result r;
    r.sequence = number(s, p);
    if (r.sequence == 0 || r.sequence > next.sequence_ || !sequences.insert(r.sequence).second)
      throw std::runtime_error("invalid checkpoint sequence");
    auto accepted = number(s, p);
    if (accepted > 1)
      throw std::runtime_error("invalid checkpoint result");
    r.accepted = accepted != 0;
    r.code = textfield(s, p);
    auto count = number(s, p);
    if (count > s.size() / 48)
      throw std::runtime_error("invalid checkpoint events");
    for (std::uint64_t j = 0; j < count; ++j) {
      Event e;
      e.type = textfield(s, p);
      e.symbol = textfield(s, p);
      e.order_id = textfield(s, p);
      e.contra_id = textfield(s, p);
      e.price = (std::int64_t)number(s, p);
      e.qty = (std::int64_t)number(s, p);
      r.events.push_back(std::move(e));
    }
    if (!next.dedup_.emplace(std::make_pair(client, request), std::make_pair(canonical, r)).second)
      throw std::runtime_error("duplicate checkpoint request");
  }
  if (p != s.size())
    throw std::runtime_error("checkpoint trailing bytes");
  static_assert(std::is_nothrow_move_constructible<Engine>::value,
                "restore requires noexcept move");
  this->~Engine();
  new (this) Engine(std::move(next));
}

} // namespace asterbook
