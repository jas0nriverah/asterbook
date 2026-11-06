# 10. Offline feed reconstruction

**Context.** Feed execution messages describe reported market events; they are not incoming orders for the simulator's matching policy.

**Decision.** The optional offline ITCH subset independently applies supported add, execution, cancel, delete and replace messages to a displayed book. Unsupported or inconsistent messages stop processing. It does not call the matching engine. See [ITCH scope](../itch.md) and [reconstructor](../../reference/itch.py).

**Alternatives and consequences.** Re-matching feed executions would invent trades and misrepresent reconstruction. This subset has custom file framing and no live transport, gap recovery or full market view; it requires a complete, ordered stream of supported updates.
