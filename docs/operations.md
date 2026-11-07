# Operations

Use persistent local storage and an unprivileged service account. Keep code, configuration, and CA material protected from that account. Start from [server.conf.example](../deploy/server.conf.example); validate it with `asterbook validate-config FILE`. Stop with SIGTERM and wait for exit.

## TLS and access

Mutual TLS is required. Client certificate common names identify clients; only names in the admin policy can run administrative commands. Keys must be regular files with no group/other permissions. The server rejects expired/not-yet-valid leaf certificates. Keep development certificates out of deployments.

Rotate certificates, CA/CRL, and admin policy by validating new files and restarting. A configured CRL must be current and CA-signed. Restart disconnects revoked sessions. Without a CRL, rotate the trusted CA. `--insecure-local` is loopback-only and must not be exposed through a proxy.

## Replication and failover

Provision two nodes with the same shard count, capacities, and initial data. Use a dedicated client-auth certificate with common name `replicator`; keep its key on the primary. Use a separate replication CA if practical. Start the standby first, adding:

```ini
standby-identity=replicator
```

Add these options to the primary's configuration:

```ini
replica-host=192.0.2.20
replica-port=9000
replica-name=standby.example.internal
replica-cert=/etc/asterbook/replicator.crt
replica-key=/etc/asterbook/replicator.key
replica-ca=/etc/asterbook/standby-ca.crt
```

`replica-host` is an IPv4 address; `replica-name` must match the standby server certificate. Both nodes still need their normal TLS server credentials and admin policies. Start empty nodes together, or seed a stopped node using a verified offline backup. Startup rejects different state, capacities, shard counts, or a peer that is not a standby.

To fail over:

1. Fence the old primary: stop it and prevent restart, or isolate/power it off using infrastructure controls. A timeout alone is not fencing.
2. Stop the standby. Restore a verified copy of its data to a replacement standby and start that replacement with `standby-identity=replicator`.
3. Remove `standby-identity` from the promoted node and configure its `replica-*` options for the replacement. Start it, check authenticated readiness, and redirect clients.
4. Retry ambiguous requests with their original client/request IDs and payloads. Re-seed the old primary before returning it as a standby.

For an explicit single-node recovery, omit the promoted node's `replica-*` options. Subsequent writes then have only one durable copy. Do not run two writable primaries. Neither certificate identity nor a local file lock fences a different host.

## Compaction and backups

Send authenticated `V1 COMPACT` to compact each shard at a queue barrier. It preserves deduplication and order IDs, blocks that shard while writing, and requires free space for both journal copies. Compact nodes independently. Never delete journals or lock files manually. Older binaries cannot recover compacted journals; retain a pre-upgrade backup for rollback.

Backups are offline. Stop the service, then run:

```sh
python3 tools/recovery.py backup --data-dir DATA --root BACKUPS --keep 7 --verifier build/release/recovery_verify
python3 tools/recovery.py restore --backup BACKUPS/backup-NAME --destination NEW_DATA --verifier build/release/recovery_verify
```

Backups verify hashes and replay before publication. Restore requires a new destination. Store copies off-host and rehearse restore/retry behavior. [Backup timer](../deploy/asterbook-backup.timer) and [script](../deploy/backup.sh) are examples: they stop the service and attempt to restart it even after backup failure. Configure and monitor the backup mount explicitly.

## Deployment and upgrades

Use [asterbook.service](../deploy/asterbook.service) with root-owned code/configuration, service-owned data, and private keys readable only by their owner. Build release packages on a compatible Linux baseline:

```sh
python3 scripts/package-release.py --version VERSION --build-dir build/release --output dist
python3 scripts/release.py --root /opt/asterbook install ARCHIVE.tar.gz --sha256 TRUSTED_SHA256
```

Stop and back up before activation. The installer validates the manifest and publishes `current`/`previous` links with recoverable intent. Run `status` after an interrupted upgrade; `rollback` swaps installed versions but does not downgrade data. Obtain the expected checksum independently; it is not a signature.

## Monitoring and limits

Use [monitor.service](../deploy/asterbook-monitor.service), [Prometheus scrape configuration](../deploy/prometheus-scrape.yml), and [alert rules](../deploy/alerts.yml). Give the exporter its own admin certificate and keep its unauthenticated HTTP listener private. `/healthz` checks the exporter; `/readyz` checks upstream; `/metrics` returns 503 on collection failure. Monitor standby health separately.

Defaults: 128 connections, 4 KiB requests, 32 MiB responses, 256 queued commands, 10,000 live orders and 1,000,000 history entries per shard. History has no eviction. Track disk and history capacity before exhaustion. Histograms are log2-nanosecond bin counters, not Prometheus histograms; service time excludes network latency.
