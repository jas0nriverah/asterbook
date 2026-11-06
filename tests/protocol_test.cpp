#include "asterbook/protocol.hpp"
#include <cassert>
#include <string>
using namespace asterbook;
int main() {
  Request r;
  std::string e;
  assert(parse_request("V1 NEW alice r1 XYZ a BUY GTC 100 5", r, e));
  assert(r.command.qty == 5);
  assert(parse_request("V1 CANCEL alice r2 XYZ a", r, e));
  assert(parse_request("V1 REPLACE alice r3 XYZ a b 101 3", r, e));
  assert(parse_request("V1 STATE", r, e));
  assert(!parse_request("NEW alice r1 XYZ a BUY GTC 100 5", r, e));
  assert(!parse_request("V1 NEW alice r1 XYZ a BUY GTC 0 5", r, e));
  assert(!parse_request("V1 NEW alice r1 XYZ a BUY GTC 9223372036854775808 5", r, e));
  assert(!parse_request("V1 STATE ", r, e));
  assert(!parse_request(std::string("V1 STATE\0", 9), r, e));
  assert(json_escape("\"\\\n") == "\\\"\\\\\\u000a");
  auto f = frame("x");
  assert(f.size() == 5 && f[3] == 1 && f[4] == 'x');
}
