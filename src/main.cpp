#include "asterbook/gateway.hpp"
#include "asterbook/protocol.hpp"
#include "asterbook/engine.hpp"
#include <charconv>
#include <iostream>
#include <string>
#include <fstream>
#include <set>
#include <vector>
using namespace asterbook;
int main(int argc, char **argv) {
  if (argc < 2) {
    std::cerr
        << "usage: asterbook server [--data-dir DIR] [--bind IPV4] [--port PORT] [--queue-capacity "
           "N] [--shards N] [--max-live N] [--max-history N] [--idle-timeout-ms N] "
           "[--frame-timeout-ms N] [--queue-timeout-ms N] [--batch-size N] [--allocator "
           "baseline|pooled] [--insecure-local | --tls-cert FILE --tls-key FILE --tls-ca FILE "
           "--admin-policy FILE] [--standby-identity CLIENT | --replica-host IPV4 --replica-port "
           "PORT "
           "--replica-name DNS --replica-cert FILE --replica-key FILE --replica-ca FILE] | "
           "replay\n";
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
  bool validation = mode == "validate-config";
  if (mode != "server" && !validation) {
    std::cerr << "unknown mode\n";
    return 2;
  }
  GatewayOptions o;
  std::vector<std::string> args;
  if (validation || (argc >= 3 && std::string(argv[2]) == "--config")) {
    if ((validation && argc != 3) || (!validation && argc != 4)) {
      std::cerr << "config must be the only argument\n";
      return 2;
    }
    std::ifstream in(argv[validation ? 2 : 3]);
    if (!in) {
      std::cerr << "cannot open config\n";
      return 2;
    }
    std::set<std::string> seen;
    std::string line;
    while (std::getline(in, line)) {
      if (line.empty() || line[0] == '#')
        continue;
      auto eq = line.find('=');
      if (eq == std::string::npos || eq == 0 || eq + 1 == line.size() ||
          !seen.insert(line.substr(0, eq)).second || line.find('\r') != std::string::npos) {
        std::cerr << "invalid config line\n";
        return 2;
      }
      auto key = line.substr(0, eq), value = line.substr(eq + 1);
      if (key == "insecure-local") {
        if (value == "true")
          args.push_back("--insecure-local");
        else if (value != "false") {
          std::cerr << "invalid config boolean\n";
          return 2;
        }
      } else {
        args.push_back("--" + key);
        args.push_back(value);
      }
    }
    if (in.bad()) {
      std::cerr << "cannot read config\n";
      return 2;
    }
  } else
    for (int i = 2; i < argc; ++i)
      args.emplace_back(argv[i]);
  for (size_t i = 0; i < args.size(); ++i) {
    std::string a = args[i];
    if (a == "--insecure-local") {
      o.insecure_local = true;
      continue;
    }
    if (i + 1 >= args.size()) {
      std::cerr << "missing option value\n";
      return 2;
    }
    std::string v = args[++i];
    if (a == "--allocator") {
      if (v == "baseline")
        o.allocator = AllocatorMode::Baseline;
      else if (v == "pooled")
        o.allocator = AllocatorMode::Pooled;
      else {
        std::cerr << "invalid allocator\n";
        return 2;
      }
    } else if (a == "--standby-identity")
      o.standby_identity = v;
    else if (a == "--replica-host")
      o.replica_host = v;
    else if (a == "--replica-name")
      o.replica_name = v;
    else if (a == "--replica-cert")
      o.replica_cert = v;
    else if (a == "--replica-key")
      o.replica_key = v;
    else if (a == "--replica-ca")
      o.replica_ca = v;
    else if (a == "--tls-cert")
      o.tls_cert = v;
    else if (a == "--tls-key")
      o.tls_key = v;
    else if (a == "--tls-ca")
      o.tls_ca = v;
    else if (a == "--tls-crl")
      o.tls_crl = v;
    else if (a == "--admin-policy")
      o.admin_policy = v;
    else if (a == "--data-dir")
      o.data_dir = v;
    else if (a == "--bind")
      o.bind = v;
    else if (a == "--port" || a == "--replica-port" || a == "--queue-capacity" || a == "--shards" ||
             a == "--max-live" || a == "--max-history" || a == "--idle-timeout-ms" ||
             a == "--frame-timeout-ms" || a == "--queue-timeout-ms" || a == "--batch-size") {
      unsigned long n = 0;
      auto [p, e] = std::from_chars(v.data(), v.data() + v.size(), n);
      if (e != std::errc{} || p != v.data() + v.size() || n == 0 ||
          ((a == "--port" || a == "--replica-port") && n > 65535) ||
          (a == "--batch-size" && n > 1024) || (a == "--queue-capacity" && n > 65536) ||
          (a == "--shards" && n > 16) || (a == "--max-live" && n > 10000) ||
          (a == "--max-history" && n > 1000000) ||
          ((a == "--idle-timeout-ms" || a == "--frame-timeout-ms" || a == "--queue-timeout-ms") &&
           n > 3600000)) {
        std::cerr << "invalid numeric option\n";
        return 2;
      }
      if (a == "--replica-port")
        o.replica_port = static_cast<uint16_t>(n);
      else if (a == "--port")
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
  if (validation)
    return validate_gateway_options(o) ? 0 : 1;
  return run_gateway(o);
}
