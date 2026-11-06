#pragma once
#include <openssl/ssl.h>
#include <memory>
#include <set>
#include <string>
namespace asterbook {
struct TlsContextDeleter {
  void operator()(SSL_CTX *p) const { SSL_CTX_free(p); }
};
using TlsContext = std::unique_ptr<SSL_CTX, TlsContextDeleter>;
TlsContext make_tls_context(const std::string &cert, const std::string &key, const std::string &ca);
std::set<std::string> load_admins(const std::string &path);
std::string peer_identity(SSL *ssl);
}
