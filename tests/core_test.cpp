#include "asterbook/engine.hpp"
#include <cassert>
#include <limits>
#include <stdexcept>
using namespace asterbook;
Command order(std::string id, std::string req, Side side, int price, int qty,
              std::string owner = "a", std::string sym = "X") {
  Command c;
  c.client = owner;
  c.request = req;
  c.symbol = sym;
  c.id = id;
  c.side = side;
  c.price = price;
  c.qty = qty;
  return c;
}
int main() {
  {
    Engine e;
    auto a = order("s1", "1", Side::Sell, 100, 4);
    assert(e.execute(a).accepted);
    auto b = order("s2", "2", Side::Sell, 100, 4);
    assert(e.execute(b).accepted);
    auto buy = order("b1", "3", Side::Buy, 100, 6, "b");
    auto r = e.execute(buy);
    assert(r.accepted && r.events.size() == 2 && r.events[0].contra_id == "s1" &&
           r.events[1].contra_id == "s2");
    assert(r.events[0].qty == 4 && r.events[1].qty == 2 && e.validate());
    assert(e.execute(buy).duplicate);
    buy.qty = 7;
    assert(e.execute(buy).code == "REQUEST_CONFLICT");
    Command c = buy;
    c.op = Op::Cancel;
    c.request = "4";
    c.id = "s2";
    assert(e.execute(c).code == "NOT_OWNER");
    c.client = "a";
    c.request = "5";
    c.op = Op::Replace;
    c.new_id = "z";
    c.qty = 0;
    assert(e.execute(c).code == "INVALID_PRICE_OR_QTY");
    c.request = "6";
    c.op = Op::Cancel;
    assert(e.execute(c).accepted);
    a.request = "7";
    assert(e.execute(a).code == "DUPLICATE_ORDER_ID");
  }
  {
    Engine e;
    auto s1 = order("s1", "1", Side::Sell, 101, 2);
    auto s2 = order("s2", "2", Side::Sell, 100, 2);
    auto sy = order("sy", "3", Side::Sell, 1, 10, "a", "Y");
    assert(e.execute(s1).accepted && e.execute(s2).accepted && e.execute(sy).accepted);
    auto b = order("b", "4", Side::Buy, 101, 6, "b");
    b.tif = Tif::Ioc;
    auto r = e.execute(b);
    assert(r.events.size() == 3 && r.events[0].contra_id == "s2" && r.events[1].contra_id == "s1" &&
           r.events[2].type == "EXPIRE" && r.events[2].qty == 2);
    assert(r.events[0].qty + r.events[1].qty + r.events[2].qty == b.qty && e.validate());
    b.id = "b2";
    b.request = "5";
    r = e.execute(b);
    assert(r.events.size() == 1 && r.events[0].type == "EXPIRE");
  }
  {
    Engine e;
    auto a = order("a", "1", Side::Sell, 100, 5);
    auto b = order("b", "2", Side::Sell, 100, 5);
    e.execute(a);
    e.execute(b);
    Command rep = a;
    rep.op = Op::Replace;
    rep.request = "3";
    rep.new_id = "a2";
    rep.side = Side::Buy;
    rep.tif = Tif::Ioc;
    assert(e.execute(rep).code == "INVALID_COMMAND");
    rep.request = "3b";
    rep.tif = Tif::Gtc;
    auto r = e.execute(rep);
    assert(r.accepted);
    auto take = order("t", "4", Side::Buy, 100, 6, "t");
    r = e.execute(take);
    assert(r.events[0].contra_id == "b" && r.events[1].contra_id == "a2");
  }
  {
    Engine e(1, 2);
    auto a = order("a", "1", Side::Buy, 10, 1);
    assert(e.execute(a).accepted);
    auto b = order("b", "2", Side::Buy, 10, 1);
    assert(e.execute(b).code == "LIVE_CAPACITY");
    b.request = "3";
    assert(e.execute(b).code == "HISTORY_CAPACITY");
    assert(e.execute(a).duplicate);
  }
  {
    Engine e(10, 2);
    auto a = order("a", "1", Side::Buy, 10, 1);
    a.tif = Tif::Ioc;
    assert(e.execute(a).accepted);
    a.id = "b";
    a.request = "2";
    assert(e.execute(a).accepted);
    a.id = "c";
    a.request = "3";
    assert(e.execute(a).code == "HISTORY_CAPACITY");
  }
  {
    Engine e;
    auto c = order("x", "1", Side::Buy, 10, 1);
    c.op = (Op)99;
    assert(e.execute(c).code == "INVALID_COMMAND");
    c.op = Op::New;
    c.request = "2";
    c.price = std::numeric_limits<std::int64_t>::max();
    assert(e.execute(c).accepted);
    c.request = "3";
    c.id = "q\"z";
    assert(e.execute(c).accepted);
    assert(e.state().find("q\\\"z") != std::string::npos);
  }
  {
    Engine source;
    assert(source.execute(order("bid", "1", Side::Buy, 10, 1)).accepted);
    assert(source.execute(order("ask", "2", Side::Sell, 20, 1)).accepted);
    auto checkpoint = source.checkpoint();
    // The order record contains length-prefixed id, owner and symbol, followed by side and price.
    auto pos = checkpoint.find(std::string("ask") + std::string("\1\0\0\0\0\0\0\0", 8) + "a");
    assert(pos != std::string::npos);
    pos += 3 + 8 + 1 + 8 + 1 + 8;
    assert(static_cast<unsigned char>(checkpoint[pos]) == 20);
    checkpoint[pos] = 5;
    Engine target;
    auto before = target.checkpoint();
    bool rejected = false;
    try {
      target.restore(checkpoint);
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    assert(rejected && target.checkpoint() == before);
  }
  {
    Engine baseline(EngineOptions{100, 10000, AllocatorMode::Baseline});
    Engine pooled(EngineOptions{100, 10000, AllocatorMode::Pooled});
    for (int i = 0; i < 500; ++i) {
      auto id = "recycle" + std::to_string(i);
      auto add = order(id, "a" + std::to_string(i), Side::Buy, 10, 1);
      assert(baseline.execute(add).accepted && pooled.execute(add).accepted);
      auto cancel = add;
      cancel.op = Op::Cancel;
      cancel.request = "c" + std::to_string(i);
      assert(baseline.execute(cancel).accepted && pooled.execute(cancel).accepted);
      assert(baseline.validate() && pooled.validate());
      if (i == 250)
        pooled.restore(pooled.checkpoint());
    }
    assert(baseline.checkpoint() == pooled.checkpoint());
    Engine moved(std::move(pooled));
    assert(moved.checkpoint() == baseline.checkpoint());
  }
}
