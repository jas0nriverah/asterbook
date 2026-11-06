#include "asterbook/protocol.hpp"
#include <charconv>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>
namespace asterbook {
namespace {
bool token_ok(std::string_view s) {
  if (s.empty() || s.size() > 64)
    return false;
  for (unsigned char c : s)
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' ||
          c == '-' || c == '.'))
      return false;
  return true;
}
bool number(std::string_view s, int64_t &n) {
  if (s.empty() || s.size() > 19 || (s.size() > 1 && s[0] == '0'))
    return false;
  for (char c : s)
    if (c < '0' || c > '9')
      return false;
  auto r = std::from_chars(s.data(), s.data() + s.size(), n);
  return r.ec == std::errc{} && r.ptr == s.data() + s.size() && n > 0;
}
}
bool parse_request(std::string_view input, Request &out, std::string &error) {
  error = "BAD_REQUEST";
  if (input.size() > 4096)
    return false;
  if (!input.starts_with("V1 "))
    return false;
  input.remove_prefix(3);
  Request r;
  std::vector<std::string_view> t;
  size_t pos = 0;
  while (pos < input.size()) {
    size_t end = input.find(' ', pos);
    if (end == std::string_view::npos)
      end = input.size();
    if (end == pos || t.size() >= 10)
      return false;
    t.push_back(input.substr(pos, end - pos));
    pos = end + 1;
    if (end < input.size() && pos == input.size())
      return false;
  }
  if (t.size() == 1 && t[0] == "STATE") {
    r.kind = RequestKind::State;
    out = std::move(r);
    return true;
  }
  if (t.size() == 1 && t[0] == "CHECKPOINT") {
    r.kind = RequestKind::Checkpoint;
    out = std::move(r);
    return true;
  }
  if (t.size() == 1 && t[0] == "METRICS") {
    r.kind = RequestKind::Metrics;
    out = std::move(r);
    return true;
  }
  if (t.empty())
    return false;
  if (t[0] == "NEW" && t.size() == 9) {
    r.command.op = Op::New;
    if (t[5] == "BUY")
      r.command.side = Side::Buy;
    else if (t[5] == "SELL")
      r.command.side = Side::Sell;
    else
      return false;
    if (t[6] == "GTC")
      r.command.tif = Tif::Gtc;
    else if (t[6] == "IOC")
      r.command.tif = Tif::Ioc;
    else
      return false;
    if (!number(t[7], r.command.price) || !number(t[8], r.command.qty))
      return false;
  } else if (t[0] == "CANCEL" && t.size() == 5)
    r.command.op = Op::Cancel;
  else if (t[0] == "REPLACE" && t.size() == 8) {
    r.command.op = Op::Replace;
    if (!token_ok(t[5]) || !number(t[6], r.command.price) || !number(t[7], r.command.qty))
      return false;
    r.command.new_id = std::string(t[5]);
  } else
    return false;
  for (size_t i = 1; i <= 4; ++i)
    if (!token_ok(t[i]))
      return false;
  r.command.client = std::string(t[1]);
  r.command.request = std::string(t[2]);
  r.command.symbol = std::string(t[3]);
  r.command.id = std::string(t[4]);
  out = std::move(r);
  return true;
}
std::string json_escape(std::string_view s) {
  std::string o;
  static const char hex[] = "0123456789abcdef";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') {
      o += '\\';
      o += char(c);
    } else if (c < 32 || c >= 127) {
      o += "\\u00";
      o += hex[c >> 4];
      o += hex[c & 15];
    } else
      o += char(c);
  }
  return o;
}
std::string error_json(std::string_view code) {
  return "{\"ok\":false,\"error\":\"" + json_escape(code) + "\"}";
}
std::string result_json(const Result &r) {
  std::string s = "{\"sequence\":" + std::to_string(r.sequence) +
                  ",\"accepted\":" + (r.accepted ? "true" : "false") + ",\"code\":\"" +
                  json_escape(r.code) + "\",\"duplicate\":" + (r.duplicate ? "true" : "false") +
                  ",\"events\":[";
  bool first = true;
  for (const auto &e : r.events) {
    if (!first)
      s += ',';
    first = false;
    s += "{\"type\":\"" + json_escape(e.type) + "\",\"symbol\":\"" + json_escape(e.symbol) +
         "\",\"order_id\":\"" + json_escape(e.order_id) + "\",\"contra_id\":\"" +
         json_escape(e.contra_id) + "\",\"price\":" + std::to_string(e.price) +
         ",\"qty\":" + std::to_string(e.qty) + "}";
  }
  return s + "]}";
}
std::string frame(std::string_view p) {
  if (p.size() > std::numeric_limits<uint32_t>::max())
    throw std::length_error("frame too large");
  uint32_t n = static_cast<uint32_t>(p.size());
  std::string o;
  o.reserve(p.size() + 4);
  for (int i = 3; i >= 0; --i)
    o.push_back(char(n >> (i * 8)));
  o.append(p);
  return o;
}
}
