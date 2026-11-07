#!/usr/bin/env python3
"""Focused boundary and denominator tests for tools/slo_report.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import slo_report


def request(scheduled, latency, outcome="ok", phase="normal", **extra):
    row = {
        "kind": "request",
        "scheduled_ns": scheduled,
        "phase": phase,
        "expected": True,
        "outcome": outcome,
        "code": "" if outcome == "ok" else outcome.upper(),
        "rate": 1,
        "client": "canary",
        "seq": scheduled,
    }
    if outcome != "generator_drop":
        row.update(start_ns=scheduled, end_ns=scheduled + latency)
    row.update(extra)
    return row


def probe(scheduled, latency, ok=True, phase="normal", **extra):
    row = {
        "kind": "probe",
        "scheduled_ns": scheduled,
        "start_ns": scheduled,
        "end_ns": scheduled + latency,
        "ok": ok,
        "phase": phase,
    }
    row.update(extra)
    return row


def run():
    # Exactly 50 ms is a good request event; one nanosecond over is bad.
    report = slo_report.analyze(
        [request(0, 50_000_000), request(100_000_000, 50_000_001)],
        expected_counts={("normal", "request"): 2},
        duration_seconds=2,
        offered_rate=1,
    )
    normal = report["phases"]["normal"]["request"]
    assert normal["expected_records"] == 2
    assert normal["good_events"] == 1
    assert normal["bad_events"] == 1
    assert normal["good_ratio"] == 0.5
    assert normal["bad_by_outcome_or_code"] == {"slow_response_over_50ms": 1}
    assert normal["response_p99_ms"] == 50
    assert normal["good_throughput_over_offered"] == 0.5
    assert normal["accepted_events"] == 2
    assert normal["accepted_throughput_over_offered"] == 1.0
    assert report["normal_experiment_pass"] is False

    # One valid-but-slow response consumes the 50 ms SLI budget, but is not an
    # execution error. With 999/1000 fast good events and adequate p99 and
    # throughput, the declared normal-phase checks pass.
    rows = [
        request(i * 1_000_000, 50_000_001 if i == 999 else 1_000_000,
                rate=1000)
        for i in range(1000)
    ]
    report = slo_report.analyze(
        rows,
        expected_counts={("normal", "request"): 1000},
        duration_seconds=1,
        offered_rate=1000,
    )
    normal = report["phases"]["normal"]["request"]
    assert normal["good_ratio"] == 0.999
    assert normal["response_p99_ms"] == 1
    assert normal["slow_responses_over_50ms"] == 1
    assert normal["execution_errors"] == 0
    assert normal["accepted_events"] == 1000
    assert normal["accepted_throughput_rps"] == 1000
    assert normal["accepted_throughput_over_offered"] == 1
    assert normal["good_throughput_rps"] == 999
    assert normal["good_throughput_over_offered"] == 0.999
    assert report["normal_experiment_pass"] is True

    # The production capacity harness logs integer client IDs and nullable
    # success codes; accept those normalized values.
    production_shape = request(0, 1_000_000)
    production_shape["client"] = 12
    production_shape["code"] = None
    assert slo_report.analyze([production_shape])["overall"]["request"]["good_events"] == 1

    # Unexpected business rejection, mismatch and transport failure are all bad
    # even when they return promptly; generator drop has no response latency.
    rows = [
        request(0, 1_000_000, "unexpected_rejection"),
        request(10, 2_000_000, "mismatch"),
        request(20, 3_000_000, "transport_error"),
        request(30, 0, "generator_drop"),
    ]
    summary = slo_report.analyze(rows)["overall"]["request"]
    assert summary["good_events"] == 0
    assert summary["bad_events"] == 4
    assert summary["generator_drops"] == 1
    assert summary["latency"]["bad"]["samples"] == 3
    assert summary["bad_by_outcome_or_code"] == {
        "GENERATOR_DROP": 1,
        "MISMATCH": 1,
        "TRANSPORT_ERROR": 1,
        "UNEXPECTED_REJECTION": 1,
    }
    assert summary["bad_by_outcome"] == {
        "generator_drop": 1,
        "mismatch": 1,
        "transport_error": 1,
        "unexpected_rejection": 1,
    }

    # Expected-but-missing request/probe slots count as bad; false probes never
    # disappear from the denominator, including failed fault-phase samples.
    report = slo_report.analyze(
        [
            request(0, 1_000_000),
            probe(0, 3_000_000, True),
            probe(1_000_000_000, 4_000_000, False, phase="restart", code="timeout"),
        ],
        expected_counts={
            ("normal", "request"): 2,
            ("normal", "probe"): 2,
            ("restart", "probe"): 2,
        },
        duration_seconds=60,
        offered_rate=1,
    )
    all_req = report["overall"]["request"]
    assert all_req["observed_expected_records"] == 1
    assert all_req["missing_records"] == 1
    assert all_req["bad_events"] == 1
    normal_probe = report["phases"]["normal"]["probe"]
    restart_probe = report["phases"]["restart"]["probe"]
    assert normal_probe["missing_records"] == 1
    assert normal_probe["bad_events"] == 1
    assert restart_probe["bad_by_outcome_or_code"] == {
        "missing_sample": 1,
        "timeout": 1,
    }
    assert restart_probe["bad_by_outcome"] == {
        "missing_sample": 1,
        "probe_failed": 1,
    }
    assert restart_probe["good_events"] == 0
    # Probe types have separate schedules and denominators even when their
    # timestamps interleave in the same phase.
    report = slo_report.analyze(
        [
            probe(0, 2_000_000, True, check="authenticated_ready"),
            probe(100, 3_000_000, False, check="transaction", code="timeout"),
        ],
        expected_counts={
            ("normal", "probe", "authenticated_ready"): 2,
            ("normal", "probe", "transaction"): 2,
        },
        duration_seconds=60,
    )
    assert report["phases"]["normal"]["probe"]["expected_records"] == 4
    assert report["phases"]["normal"]["probe"]["good_events"] == 1
    assert report["phases"]["normal"]["probe"]["missing_records"] == 2
    assert report["probe_checks"]["authenticated_ready"]["normal"]["missing_records"] == 1
    assert report["probe_checks"]["transaction"]["normal"]["bad_by_outcome_or_code"] == {
        "missing_sample": 1,
        "timeout": 1,
    }

    # expected=false is excluded; it cannot make the production in-scope
    # denominator look worse or better.
    excluded = request(5, 1, expected=False)
    report = slo_report.analyze([request(0, 1), excluded])
    assert report["overall"]["request"]["expected_records"] == 1
    assert report["overall"]["request"]["excluded_records"] == 1

    # Separate phases remain separate; fixed workload rates infer per-phase
    # throughput, while varying rates make a mixed-phase offered rate unknown.
    report = slo_report.analyze(
        [request(0, 2_000_000, phase="normal", rate=10),
         request(1_000_000_000, 2_000_000, phase="restart", rate=5)],
        duration_seconds=1,
    )
    assert report["phases"]["normal"]["request"]["offered_rate_rps"] == 10
    assert report["phases"]["restart"]["request"]["offered_rate_rps"] == 5
    assert report["overall"]["request"]["offered_rate_rps"] is None

    assert slo_report.percentile([1, 2, 3, 4], 0.99) == 3
    assert abs(
        report["thresholds"]["monthly_budget_arithmetic_only"]["error_budget_minutes"]
        - 43.2
    ) < 1e-9
    assert "not production SLO/SLA evidence" in report["interpretation"]["status"]

    # Invalid boundaries, codes, booleans and missing response timestamps fail
    # input validation rather than being silently dropped.
    invalid = request(10, 1)
    invalid["start_ns"] = 9
    try:
        slo_report.validate_record(invalid)
        raise AssertionError("accepted start before scheduled time")
    except ValueError:
        pass
    invalid = probe(0, 1)
    invalid["ok"] = 1
    try:
        slo_report.validate_record(invalid)
        raise AssertionError("accepted non-boolean probe result")
    except ValueError:
        pass
    invalid = request(0, 1)
    invalid["outcome"] = "unknown"
    try:
        slo_report.validate_record(invalid)
        raise AssertionError("accepted unknown request outcome")
    except ValueError:
        pass
    invalid = request(0, 0, "generator_drop")
    invalid["end_ns"] = 1
    try:
        slo_report.validate_record(invalid)
        raise AssertionError("accepted response timestamps for generator drop")
    except ValueError:
        pass


if __name__ == "__main__":
    run()
