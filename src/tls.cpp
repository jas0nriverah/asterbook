#include "asterbook/tls.hpp"
#include <openssl/x509.h>
#include <fstream>
#include <sys/stat.h>
#include <stdexcept>
namespace asterbook {
static bool valid_identity(const std::string &s) {
  if (s.empty() || s.size() > 64)
    return false;
  for (unsigned char c : s)
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' ||
          c == '-' || c == '.'))
      return false;
  return true;
}
TlsContext make_tls_context(const std::string &cert, const std::string &key,
                            const std::string &ca) {
  struct stat key_stat{};
  if (stat(key.c_str(), &key_stat) != 0 || !S_ISREG(key_stat.st_mode) || (key_stat.st_mode & 0077))
    throw std::runtime_error(
        "TLS private key must be a regular file without group or world permissions");
  TlsContext ctx(SSL_CTX_new(TLS_server_method()));
  if (!ctx || SSL_CTX_set_min_proto_version(ctx.get(), TLS1_2_VERSION) != 1 ||
      SSL_CTX_use_certificate_chain_file(ctx.get(), cert.c_str()) != 1 ||
      SSL_CTX_use_PrivateKey_file(ctx.get(), key.c_str(), SSL_FILETYPE_PEM) != 1 ||
      SSL_CTX_check_private_key(ctx.get()) != 1 ||
      SSL_CTX_load_verify_locations(ctx.get(), ca.c_str(), nullptr) != 1)
    throw std::runtime_error("invalid TLS certificate, key, or client CA");
  SSL_CTX_set_verify(ctx.get(), SSL_VERIFY_PEER | SSL_VERIFY_FAIL_IF_NO_PEER_CERT, nullptr);
  return ctx;
}
std::set<std::string> load_admins(const std::string &path) {
  std::ifstream in(path);
  if (!in)
    throw std::runtime_error("cannot open admin policy");
  std::set<std::string> admins;
  std::string line;
  while (std::getline(in, line)) {
    if (!valid_identity(line) || !admins.insert(line).second)
      throw std::runtime_error("invalid admin policy: expected one unique identity per line");
  }
  if (in.bad())
    throw std::runtime_error("cannot read admin policy");
  return admins;
}
std::string peer_identity(SSL *ssl) {
  if (SSL_get_verify_result(ssl) != X509_V_OK)
    return {};
  X509 *cert = SSL_get1_peer_certificate(ssl);
  if (!cert)
    return {};
  X509_NAME *subject = X509_get_subject_name(cert);
  int idx = X509_NAME_get_index_by_NID(subject, NID_commonName, -1);
  std::string identity;
  if (idx >= 0 && X509_NAME_get_index_by_NID(subject, NID_commonName, idx) < 0) {
    ASN1_STRING *s = X509_NAME_ENTRY_get_data(X509_NAME_get_entry(subject, idx));
    unsigned char *utf8 = nullptr;
    int n = ASN1_STRING_to_UTF8(&utf8, s);
    if (n > 0)
      identity.assign(reinterpret_cast<char *>(utf8), static_cast<size_t>(n));
    OPENSSL_free(utf8);
  }
  X509_free(cert);
  return valid_identity(identity) ? identity : std::string{};
}
}
