#pragma once
#include <cstdint>
#include <string>
#include <vector>
namespace asterbook {
enum class Op { New, Cancel, Replace };
enum class Side { Buy, Sell };
enum class Tif { Gtc, Ioc };
struct Command {
  Op op = Op::New;
  std::string client, request, symbol, id, new_id;
  Side side = Side::Buy;
  Tif tif = Tif::Gtc;
  std::int64_t price = 0, qty = 0;
};
struct Event {
  std::string type, symbol, order_id, contra_id;
  std::int64_t price = 0, qty = 0;
};
struct Result {
  std::uint64_t sequence = 0;
  bool accepted = false;
  std::string code;
  std::vector<Event> events;
  bool duplicate = false;
};
}
