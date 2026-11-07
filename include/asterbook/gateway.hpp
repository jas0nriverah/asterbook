#pragma once
#include "asterbook/engine.hpp"
#include <cstdint>
#include <cstddef>
#include <string>
namespace asterbook {
struct GatewayOptions {
  std::string standby_identity;
  std::string replica_host, replica_name, replica_cert, replica_key, replica_ca;
  uint16_t replica_port = 9000;
  std::string data_dir = "asterbook-data";
  std::string bind = "127.0.0.1";
  uint16_t port = 9000;
  bool insecure_local = false;
  std::string tls_cert, tls_key, tls_ca, tls_crl, admin_policy;
  size_t handshake_timeout_ms = 10000;
  size_t queue_capacity = 256;
  size_t batch_size = 1;
  AllocatorMode allocator = AllocatorMode::Baseline;
  size_t shards = 1;
  size_t max_live = 10000;
  size_t max_history = 1000000;
  size_t idle_timeout_ms = 30000;
  size_t frame_timeout_ms = 30000;
  size_t queue_timeout_ms = 5000;
};
bool validate_gateway_options(const GatewayOptions &options);
int run_gateway(const GatewayOptions &options);
}
