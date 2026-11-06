#pragma once
#include <cstdint>
#include <cstddef>
#include <string>
namespace asterbook {
struct GatewayOptions {
  std::string data_dir = "asterbook-data";
  std::string bind = "127.0.0.1";
  uint16_t port = 9000;
  bool unsafe_non_loopback = false;
  size_t queue_capacity = 256;
  size_t shards = 1;
  size_t max_live = 10000;
  size_t max_history = 1000000;
  size_t idle_timeout_ms = 30000;
  size_t frame_timeout_ms = 30000;
  size_t queue_timeout_ms = 5000;
};
int run_gateway(const GatewayOptions &options);
}
