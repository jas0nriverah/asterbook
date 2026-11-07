# Experimental qualification

bench/capacity.py drives the real mTLS protocol with independent, phased clients.
It measures one scheduled protocol command per slot, including connection setup
and any wait until a decoded, validated response. This differs from open_loop.py
items, where a cancel item contains two commands.

Linux records process metrics from `/proc`; macOS uses `ps` for CPU and memory, with unavailable I/O, peak RSS, descriptor counts and affinity recorded as null. Shared-runner CI checks correctness and recovery, not latency or throughput qualification.

For disposable data only:

    python3 tools/dev_certs.py /tmp/qualification-certs
    python3 bench/capacity.py --server build/release/asterbook \
      --data-dir /tmp/new-qualification-data --output /tmp/new-qualification-results \
      --cert-dir /tmp/qualification-certs --rate 3000 --seconds 1800 \
      --clients 120 --shards 16 --history 1000000 --phase normal

Both data and result directories must be new. The mixed cycle submits a limit
order, replaces it, cancels it, adds a contra order, crosses it with IOC and
expires an unmatched IOC. Each client has an independent symbol; live orders
stay bounded. The --hot option routes independent symbols to the same actual
shard. The --profile ioc option provides independent admission-limit requests.
All expected event payloads are checked. Failed or ambiguous commands retry the
same request identity on later scheduled slots, preserving cycle correctness.
Generator drops remain bad scheduled outcomes rather than disappearing.

The gateway permits 128 simultaneous clients and refills each connection at
50 commands/s, with a burst allowance of 100. The harness reserves admin/probe
slots by limiting data clients to 120. It records actual connections and uses
TCP_NODELAY for interactive frames; the gateway also uses TCP_NODELAY so TLS
handshake/session-ticket traffic does not delay the first short reply.

The resource guard stops a run below 10 GiB of free disk; use `--min-free-disk-bytes` for an explicit deployment-specific floor. CI uses 16 MiB for its small disposable fixture.

Default experiment gates are p99 scheduled-to-response latency at most 10 ms,
at least 99.9% correct responses within 50 ms, at least 99% of offered accepted
throughput, zero execution errors or generator drops, and recovery within 30 s.
Those are test hypotheses, not an SLA. A failure remains in summary.json and
the command exits nonzero. Raw JSONL includes failed responses and scheduling
lag; probes distinguish authenticated READY from a mutating transaction.

Every five seconds the harness records CPU, RSS, I/O, file growth, free disk,
queue metrics and per-shard history. It stops at 80% history usage, 16 GiB RSS
or less than 10 GiB free space. Snapshot/restart state and history counts are
compared; sampled acknowledged commands must return their exact cached payload
with the duplicate flag after restart. TLS identities are preserved on retries.

History does not evict or compact. With 120 equally loaded clients over
16 shards, the busiest shard has eight clients. At 3,000 commands/s that is
200 commands/s on that shard: a fresh 1,000,000-entry shard reaches 80% around
66.7 minutes and fills around 83.3 minutes, before allowing for canaries.
These are capacity arithmetic, not measured lifetime guarantees. Maintenance
and deduplication-retention semantics must be designed before indefinite use.
READY checks workers and journal synchronization; it does not promise room for
new history. The saturation regression verifies that old duplicates remain
available while new commands return HISTORY_CAPACITY.

Use tools/slo_report.py on raw request/probe records with explicit phase
durations and expected counts. Missing expected samples count as bad; probe
checks stay separate. Report the full test window as well as baseline and
injected-fault phases. Its 30-day error budget is arithmetic only.

The alert rules include missing scrape-target detection in addition to up=0.
Validate them with:

    promtool check rules deploy/alerts.yml
    promtool test rules tests/alerts.test.yml

The service histograms are noncumulative log2-nanosecond bins, not Prometheus
classic histogram buckets. Their service-time tail is not end-to-end latency.

For durability qualification, separately verify acknowledged commands across
a flush-respecting volatile-cache crash model and test a negative control that
actually loses unflushed data. A process kill alone leaves host/kernel caches
intact. A modeled guest/device crash cannot certify physical controller power
loss; target-hardware tests and long external operational observation remain
separate evidence requirements.
