#pragma once
#include "asterbook/gateway.hpp"
#include "asterbook/tls.hpp"
namespace asterbook {
std::string checkpoint_digest(const std::string &checkpoint);
std::string command_wire(const Command &command);
class Replica {
public:
  explicit Replica(const GatewayOptions &options);
  std::string request(const std::string &payload) const;

private:
  TlsContext context_;
  std::string host_, name_;
  uint16_t port_;
};
}
