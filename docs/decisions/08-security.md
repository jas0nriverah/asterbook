# 8. Trust boundary

**Context.** The simulator accepts commands and administrative operations over TCP but has no identity infrastructure.

**Decision.** Bind to IPv4 loopback by default; non-loopback binding requires an explicit unsafe flag. There is no authentication, authorization, TLS or tenant isolation. A command's client field only enforces book ownership under a trusted-client assumption. See [gateway](../../src/gateway.cpp) and [operations](../operations.md).

**Alternatives and consequences.** A public listener without transport security and authenticated principals would expose order mutation and checkpoints to anyone who can connect. The override is for controlled environments; it does not add protection. This is not a security or exchange certification.
