# Transport and authorization

Server startup requires TLS credentials and an admin policy unless `--insecure-local` is explicitly selected. TLS uses OpenSSL, requires TLS 1.2 or later, and verifies client certificates against the configured client CA. The server key must be a regular file inaccessible to group and other users. Client certificate subjects must contain exactly one common name, using the same 1–64 character identifier grammar as protocol clients. The certificate authority must only issue client certificates to identities it controls; possession of a trusted certificate grants that identity's authority. Certificate revocation and live policy reload are not implemented. Rotate certificates and restart the service to remove access.

The authenticated common name must equal the client field in each order command. A mismatch returns `IDENTITY_MISMATCH` before admission. `STATE`, `CHECKPOINT`, and `METRICS` require the identity to appear in the admin policy, or return `FORBIDDEN`. The policy file contains one unique identity per line, with no blank lines or comments; an empty file grants no admins. Invalid or missing configuration fails startup. Protect the CA, private keys and policy file with operating system access controls.

`--insecure-local` accepts plaintext only on an IPv4 loopback bind. It supplies no authentication or authorization and is for local development and tests only. Do not expose its port through a proxy or tunnel. There is no plaintext non-loopback mode.

For a disposable local demonstration:

```sh
python3 tools/dev_certs.py /tmp/asterbook-dev-certs
asterbook server --data-dir /tmp/asterbook-demo --tls-cert /tmp/asterbook-dev-certs/server.crt --tls-key /tmp/asterbook-dev-certs/server.key --tls-ca /tmp/asterbook-dev-certs/ca.crt --admin-policy /tmp/asterbook-dev-certs/admins.txt
python3 tools/client.py --host localhost --ca /tmp/asterbook-dev-certs/ca.crt --cert /tmp/asterbook-dev-certs/alice.crt --key /tmp/asterbook-dev-certs/alice.key 'NEW alice r1 XYZ o1 BUY GTC 100 1'
```

The generated CA and keys are disposable development credentials, not production trust material. The client validates the server chain and hostname. Use a production CA, independently provisioned keys, appropriate certificate lifetimes and a managed trust policy in deployment.
