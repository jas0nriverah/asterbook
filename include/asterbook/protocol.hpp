#pragma once
#include "asterbook/engine.hpp"
#include <string>
#include <string_view>
namespace asterbook {
enum class RequestKind { Command, State, Metrics, Checkpoint, Ready };
struct Request {
  RequestKind kind = RequestKind::Command;
  Command command{};
};
bool parse_request(std::string_view input, Request &result, std::string &error);
std::string json_escape(std::string_view text);
std::string result_json(const Result &result);
std::string error_json(std::string_view code);
std::string frame(std::string_view payload);
}
