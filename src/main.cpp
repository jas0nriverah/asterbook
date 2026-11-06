#include "asterbook/gateway.hpp"
#include "asterbook/protocol.hpp"
#include "asterbook/engine.hpp"
#include <charconv>
#include <iostream>
#include <string>
using namespace asterbook;
int main(int argc, char **argv) {
  if (argc < 2) {
    std::cerr
        << "usage: asterbook server [--data-dir DIR] [--bind IPV4] [--port PORT] [--queue-capacity "
           "N] [--shards N] [--max-live N] [--max-history N] [--idle-timeout-ms N] "
           "[--frame-timeout-ms N] [--queue-timeout-ms N] [--batch-size N] [--allocator "
           "baseline|pooled] [--insecure-local | --tls-cert FILE --tls-key FILE --tls-ca FILE "
           "--admin-policy FILE] | replay\n";
    return 2;
  }
  std::string mode = argv[1];
  if (mode == "replay") {
    Engine engine;
    std::string line;
    while (std::getline(std::cin, line)) {
      Request r;
      std::string err;
      if (!parse_request(line, r, err))
        std::cout << error_json(err) << '\n';
      else if (r.kind == RequestKind::State)
        std::cout << engine.state() << '\n';
      else if (r.kind == RequestKind::Command)
        std::cout << result_json(engine.execute(r.command)) << '\n';
      else
        std::cout << error_json("UNSUPPORTED") << '\n';
    }
    return std::cin.bad() ? 1 : 0;
  }
  if (mode != "server") {
    std::cerr << "unknown mode\n";
    return 2;
  }
  GatewayOptions o;
  for (int i = 2; i < argc; ++i) {
    std::string a = argv[i];
    if (a == "--insecure-local") {
      o.insecure_local = true;
      continue;
    }
    if (i + 1 >= argc) {
      std::cerr << "missing option value\n";
      return 2;
    }
    std::string v = argv[++i];
    if (a == "--allocator") {
      if (v == "baseline")
        o.allocator = AllocatorMode::Baseline;
      else if (v == "pooled")
        o.allocator = AllocatorMode::Pooled;
      else {
        std::cerr << "invalid allocator\n";
        return 2;
      }
    } else if (a == "--tls-cert")
      o.tls_cert = v;
    else if (a == "--tls-key")
      o.tls_key = v;
    else if (a == "--tls-ca")
      o.tls_ca = v;
    else if (a == "--admin-policy")
      o.admin_policy = v;
    else if (a == "--data-dir")
      o.data_dir = v;
    else if (a == "--bind")
      o.bind = v;
    else if (a == "--port" || a == "--queue-capacity" || a == "--shards" || a == "--max-live" ||
             a == "--max-history" || a == "--idle-timeout-ms" || a == "--frame-timeout-ms" ||
             a == "--queue-timeout-ms" || a == "--batch-size") {
      unsigned long n = 0;
      auto [p, e] = std::from_chars(v.data(), v.data() + v.size(), n);
      if (e != std::errc{} || p != v.data() + v.size() || n == 0 || (a == "--port" && n > 65535) ||
          (a == "--batch-size" && n > 1024) || (a == "--queue-capacity" && n > 65536) ||
          (a == "--shards" && n > 16) || (a == "--max-live" && n > 10000) ||
          (a == "--max-history" && n > 1000000) ||
          ((a == "--idle-timeout-ms" || a == "--frame-timeout-ms" || a == "--queue-timeout-ms") &&
           n > 3600000)) {
        std::cerr << "invalid numeric option\n";
        return 2;
      }
      if (a == "--port")
        o.port = static_cast<uint16_t>(n);
      else if (a == "--queue-capacity")
        o.queue_capacity = n;
      else if (a == "--batch-size")
        o.batch_size = n;
      else if (a == "--shards")
        o.shards = n;
      else if (a == "--max-live")
        o.max_live = n;
      else if (a == "--max-history")
        o.max_history = n;
      else if (a == "--idle-timeout-ms")
        o.idle_timeout_ms = n;
      else if (a == "--frame-timeout-ms")
        o.frame_timeout_ms = n;
      else
        o.queue_timeout_ms = n;
    } else {
      std::cerr << "unknown option " << a << '\n';
      return 2;
    }
  }
  return run_gateway(o);
}
