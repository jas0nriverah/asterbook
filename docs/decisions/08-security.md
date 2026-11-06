# 8. Transport security

The gateway requires mutual TLS and an explicit admin allowlist by default. A verified client certificate's single common name is the command client identity; administrative commands additionally require membership in the policy file. TLS handshakes use nonblocking I/O and an absolute deadline. `--insecure-local` is an explicit, loopback-only development mode without authentication. See [security](../security.md) for configuration and limitations.
