#include "asterbook/replication.hpp"
#include "asterbook/protocol.hpp"
#include <openssl/sha.h>
#include <arpa/inet.h>
#include <netinet/tcp.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include <chrono>
#include <cerrno>
#include <stdexcept>
namespace asterbook {
std::string checkpoint_digest(const std::string &checkpoint) {
  unsigned char digest[SHA256_DIGEST_LENGTH];
  SHA256(reinterpret_cast<const unsigned char *>(checkpoint.data()), checkpoint.size(), digest);
  std::string out;
  for (auto byte : digest) {
    out += "0123456789abcdef"[byte >> 4];
    out += "0123456789abcdef"[byte & 15];
  }
  return out;
}
std::string command_wire(const Command &c) {
  auto prefix = "V1 " +
                std::string(c.op == Op::New      ? "NEW"
                            : c.op == Op::Cancel ? "CANCEL"
                                                 : "REPLACE") +
                " " + c.client + " " + c.request + " " + c.symbol + " " + c.id;
  if (c.op == Op::Cancel)
    return prefix;
  if (c.op == Op::Replace)
    return prefix + " " + c.new_id + " " + std::to_string(c.price) + " " + std::to_string(c.qty);
  return prefix + (c.side == Side::Buy ? " BUY " : " SELL ") +
         (c.tif == Tif::Gtc ? "GTC " : "IOC ") + std::to_string(c.price) + " " +
         std::to_string(c.qty);
}
Replica::Replica(const GatewayOptions &o)
    : context_(SSL_CTX_new(TLS_client_method())), host_(o.replica_host), name_(o.replica_name),
      port_(o.replica_port) {
  struct stat st{};
  if (stat(o.replica_key.c_str(), &st) || !S_ISREG(st.st_mode) || (st.st_mode & 0077))
    throw std::runtime_error("replication key permissions");
  if (!context_ || SSL_CTX_set_min_proto_version(context_.get(), TLS1_2_VERSION) != 1 ||
      SSL_CTX_load_verify_locations(context_.get(), o.replica_ca.c_str(), nullptr) != 1 ||
      SSL_CTX_use_certificate_chain_file(context_.get(), o.replica_cert.c_str()) != 1 ||
      SSL_CTX_use_PrivateKey_file(context_.get(), o.replica_key.c_str(), SSL_FILETYPE_PEM) != 1 ||
      SSL_CTX_check_private_key(context_.get()) != 1)
    throw std::runtime_error("replication TLS configuration");
  SSL_CTX_set_verify(context_.get(), SSL_VERIFY_PEER, nullptr);
}
std::string Replica::request(const std::string &payload) const {
  using Clock = std::chrono::steady_clock;
  auto deadline = Clock::now() + std::chrono::seconds(5);
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0)
    throw std::runtime_error("replica socket failed");
  struct Socket {
    int fd;
    ~Socket() { close(fd); }
  } socket_guard{fd};
  auto wait = [&](short event) {
    while (true) {
      auto left =
          std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
      if (left <= 0)
        throw std::runtime_error("replica deadline exceeded");
      pollfd p{fd, event, 0};
      int rc = poll(&p, 1, static_cast<int>(left));
      if (rc < 0 && errno == EINTR)
        continue;
      if (rc <= 0 || (p.revents & (POLLERR | POLLNVAL)))
        throw std::runtime_error("replica unavailable");
      return;
    }
  };
  if (fcntl(fd, F_SETFL, O_NONBLOCK) < 0)
    throw std::runtime_error("replica socket mode failed");
  int one = 1;
  if (setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one)))
    throw std::runtime_error("replica socket option failed");
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(port_);
  if (inet_pton(AF_INET, host_.c_str(), &address.sin_addr) != 1)
    throw std::runtime_error("replica requires IPv4 address");
  if (connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
    if (errno != EINPROGRESS)
      throw std::runtime_error("replica connect failed");
    wait(POLLOUT);
    int error = 0;
    socklen_t length = sizeof(error);
    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) || error)
      throw std::runtime_error("replica connect failed");
  }
  std::unique_ptr<SSL, decltype(&SSL_free)> ssl(SSL_new(context_.get()), SSL_free);
  if (!ssl || SSL_set_fd(ssl.get(), fd) != 1 || SSL_set1_host(ssl.get(), name_.c_str()) != 1 ||
      SSL_set_tlsext_host_name(ssl.get(), name_.c_str()) != 1)
    throw std::runtime_error("replica TLS setup failed");
  auto retry = [&](int rc) {
    int error = SSL_get_error(ssl.get(), rc);
    if (error == SSL_ERROR_WANT_READ)
      wait(POLLIN);
    else if (error == SSL_ERROR_WANT_WRITE)
      wait(POLLOUT);
    else
      throw std::runtime_error("replica TLS exchange failed");
  };
  int rc;
  while ((rc = SSL_connect(ssl.get())) != 1)
    retry(rc);
  auto bytes = frame(payload);
  std::size_t offset = 0;
  while (offset < bytes.size()) {
    rc = SSL_write(ssl.get(), bytes.data() + offset, static_cast<int>(bytes.size() - offset));
    if (rc <= 0)
      retry(rc);
    else
      offset += rc;
  }
  auto read = [&](std::size_t count) {
    std::string out(count, '\0');
    std::size_t pos = 0;
    while (pos < count) {
      if (Clock::now() >= deadline)
        throw std::runtime_error("replica deadline exceeded");
      rc = SSL_read(ssl.get(), out.data() + pos, static_cast<int>(count - pos));
      if (rc <= 0)
        retry(rc);
      else
        pos += rc;
    }
    return out;
  };
  auto header = read(4);
  uint32_t length = 0;
  for (unsigned char c : header)
    length = (length << 8) | c;
  if (!length || length > 32 * 1024 * 1024)
    throw std::runtime_error("replica invalid response length");
  return read(length);
}
}
