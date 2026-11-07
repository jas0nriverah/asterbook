#!/usr/bin/env python3
"""Summarize append-only JSONL samples for a bounded reliability experiment.

The tool reports test-window observations. It does not turn a short or
correlated experiment into production SLO/SLA evidence.
"""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

REQUEST_OUTCOMES = {
    "ok",
    "unexpected_rejection",
    "transport_error",
    "generator_drop",
    "invalid_response",
    "mismatch",
}
REQUEST_GOOD_NS = 50_000_000
REQUEST_P99_LIMIT_NS = 10_000_000
AVAILABILITY_TARGET = 0.999
THROUGHPUT_TARGET = 0.99
SLO_WINDOW_DAYS = 30


def _integer(record, field, *, optional=False):
    value = record.get(field)
    if optional and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def validate_record(record, line_number=None):
    """Validate one request or probe sample."""
    where = f"line {line_number}: " if line_number else ""
    try:
        if not isinstance(record, dict):
            raise ValueError("each JSONL row must be an object")
        kind = record.get("kind")
        if kind not in ("request", "probe"):
            raise ValueError("kind must be request or probe")
        expected = record.get("expected", True)
        if type(expected) is not bool:
            raise ValueError("expected must be a boolean")
        phase = record.get("phase", "unspecified")
        if not isinstance(phase, str) or not phase:
            raise ValueError("phase must be a nonempty string")
        scheduled = _integer(record, "scheduled_ns")
        start = _integer(record, "start_ns", optional=True)
        end = _integer(record, "end_ns", optional=True)
        if kind == "request":
            outcome = record.get("outcome")
            if outcome not in REQUEST_OUTCOMES:
                raise ValueError("unsupported request outcome")
            if outcome == "generator_drop":
                if start is not None or end is not None:
                    raise ValueError("generator_drop must not have start_ns/end_ns")
            elif start is None or end is None:
                raise ValueError("non-dropped request requires start_ns and end_ns")
            if "rate" in record:
                rate = record["rate"]
                if (isinstance(rate, bool) or not isinstance(rate, (int, float))
                        or not math.isfinite(rate) or rate <= 0):
                    raise ValueError("rate must be a positive number")
            if "seq" in record:
                _integer(record, "seq")
        else:
            if type(record.get("ok")) is not bool:
                raise ValueError("probe ok must be a boolean")
            if "check" in record and (
                not isinstance(record["check"], str) or not record["check"]
            ):
                raise ValueError("probe check must be a nonempty string")
            if start is None or end is None:
                raise ValueError("probe requires start_ns and end_ns")
        if start is not None and start < scheduled:
            raise ValueError("start_ns must be >= scheduled_ns")
        if start is not None and end < start:
            raise ValueError("end_ns must be >= start_ns")
        for field in ("code", "fault_id"):
            if field in record and record[field] is not None and not isinstance(record[field], str):
                raise ValueError(f"{field} must be a string or null")
        if "client" in record:
            client = record["client"]
            if (not isinstance(client, str)
                    and (type(client) is not int or client < 0)):
                raise ValueError("client must be a string or nonnegative integer")
        return record
    except ValueError as exc:
        raise ValueError(where + str(exc)) from exc


def percentile(values, quantile):
    """Match bench/open_loop.py's empirical floor-index percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int(quantile * (len(ordered) - 1))]


def _ms(value):
    return None if value is None else value / 1_000_000


def _latency_summary(all_ns, good_ns, bad_ns):
    def stats(values):
        return {
            "samples": len(values),
            "p50_ms": _ms(percentile(values, 0.50)),
            "p95_ms": _ms(percentile(values, 0.95)),
            "p99_ms": _ms(percentile(values, 0.99)),
        }

    return {
        "all_response_attempts": stats(all_ns),
        "good": stats(good_ns),
        "bad": stats(bad_ns),
    }


def _derive_duration(min_ns, max_ns, explicit_seconds=None):
    if explicit_seconds is not None:
        if (isinstance(explicit_seconds, bool)
                or not isinstance(explicit_seconds, (int, float))
                or not math.isfinite(explicit_seconds) or explicit_seconds <= 0):
            raise ValueError("duration_seconds must be positive")
        return float(explicit_seconds), "explicit"
    if min_ns is None or max_ns is None or max_ns <= min_ns:
        return None, "unavailable"
    duration = (max_ns - min_ns) / 1_000_000_000
    return duration, "scheduled_span_without_endpoint_inference"


class _Accumulator:
    """Keep only counters and latency values while consuming JSONL rows."""

    def __init__(self, kind):
        self.kind = kind
        self.observed = 0
        self.excluded = 0
        self.good = 0
        self.accepted = 0
        self.drops = 0
        self.codes = Counter()
        self.outcomes = Counter()
        self.rates = set()
        self.min_ns = None
        self.max_ns = None
        self.all_ns, self.good_ns, self.bad_ns = [], [], []

    def add(self, record):
        if not record.get("expected", True):
            self.excluded += 1
            return
        self.observed += 1
        scheduled = record["scheduled_ns"]
        self.min_ns = scheduled if self.min_ns is None else min(self.min_ns, scheduled)
        self.max_ns = scheduled if self.max_ns is None else max(self.max_ns, scheduled)
        if self.kind == "request" and "rate" in record:
            self.rates.add(float(record["rate"]))
        if self.kind == "request":
            end = record.get("end_ns")
            latency = None if end is None else end - scheduled
            if record["outcome"] == "ok":
                self.accepted += 1
            is_good = record["outcome"] == "ok" and latency <= REQUEST_GOOD_NS
            if record["outcome"] == "generator_drop":
                self.drops += 1
            if not is_good:
                self.outcomes[record["outcome"]] += 1
                code = record.get("code") or record["outcome"]
                if record["outcome"] == "ok" and latency > REQUEST_GOOD_NS:
                    code = "slow_response_over_50ms"
                self.codes[code] += 1
        else:
            latency = record["end_ns"] - scheduled
            is_good = record["ok"]
            if not is_good:
                self.outcomes["probe_failed"] += 1
                self.codes[record.get("code") or "probe_failed"] += 1
        if is_good:
            self.good += 1
        if latency is not None:
            self.all_ns.append(latency)
            (self.good_ns if is_good else self.bad_ns).append(latency)

    def summary(self, expected_count=None, duration_seconds=None, offered_rate=None):
        if expected_count is not None and (
            type(expected_count) is not int or expected_count < 0
        ):
            raise ValueError("expected_count must be a nonnegative integer")
        if expected_count is not None and expected_count < self.observed:
            raise ValueError("expected_count is smaller than observed expected records")
        missing = 0 if expected_count is None else expected_count - self.observed
        duration, duration_source = _derive_duration(
            self.min_ns, self.max_ns, duration_seconds
        )
        rates = set(self.rates)
        if offered_rate is not None:
            if (isinstance(offered_rate, bool)
                    or not isinstance(offered_rate, (int, float))
                    or not math.isfinite(offered_rate) or offered_rate <= 0):
                raise ValueError("offered_rate must be positive")
            rates = {float(offered_rate)}
        offered = next(iter(rates)) if len(rates) == 1 else None
        codes = self.codes.copy()
        outcomes = self.outcomes.copy()
        if missing:
            codes["missing_sample"] += missing
            outcomes["missing_sample"] += missing
        expected = self.observed + missing
        ratio = self.good / expected if expected else None
        throughput = self.good / duration if duration is not None else None
        offered_ratio = throughput / offered if throughput is not None and offered else None
        accepted_throughput = self.accepted / duration if duration is not None else None
        accepted_offered_ratio = (
            accepted_throughput / offered
            if accepted_throughput is not None and offered else None
        )
        p99_ns = percentile(self.all_ns, 0.99)
        execution_errors = sum(
            count for outcome, count in outcomes.items()
            if outcome not in ("ok", "generator_drop")
        )
        return {
            "kind": self.kind,
            "expected_records": expected,
            "observed_expected_records": self.observed,
            "missing_records": missing,
            "excluded_records": self.excluded,
            "good_events": self.good,
            "accepted_events": self.accepted,
            "bad_events": expected - self.good,
            "good_ratio": ratio,
            "bad_by_outcome_or_code": dict(sorted(codes.items())),
            "bad_by_outcome": dict(sorted(outcomes.items())),
            "execution_errors": execution_errors,
            "slow_responses_over_50ms": codes.get("slow_response_over_50ms", 0),
            "generator_drops": self.drops,
            "generator_drop_ratio": self.drops / expected if expected else None,
            "latency": _latency_summary(self.all_ns, self.good_ns, self.bad_ns),
            "response_p99_ms": _ms(p99_ns),
            "p99_sample_count": len(self.all_ns),
            "p99_low_sample_warning": len(self.all_ns) < 1000,
            "duration_seconds": duration,
            "duration_source": duration_source,
            "offered_rate_rps": offered,
            "good_throughput_rps": throughput,
            "good_throughput_over_offered": offered_ratio,
            "accepted_throughput_rps": accepted_throughput,
            "accepted_throughput_over_offered": accepted_offered_ratio,
        }


def analyze(records, *, expected_counts=None, duration_seconds=None,
            offered_rate=None, phase_durations=None):
    """Return overall and per-phase summaries.

    expected_counts maps (phase, kind) or (phase, probe, check) to scheduled
    slots. Missing slots count as bad; check expectations keep probe types apart.
    """
    expected_counts = expected_counts or {}
    for key, value in expected_counts.items():
        valid_key = (
            isinstance(key, tuple)
            and (
                (len(key) == 2 and key[1] in ("request", "probe"))
                or (len(key) == 3 and key[1] == "probe"
                    and isinstance(key[2], str) and bool(key[2]))
            )
            and isinstance(key[0], str)
        )
        if not valid_key or type(value) is not int or value < 0:
            raise ValueError(
                "expected_counts keys must be (phase, kind) or "
                "(phase, probe, check), with nonnegative counts"
            )
    for phase in {k[0] for k in expected_counts}:
        if (phase, "probe") in expected_counts and any(
            len(k) == 3 and k[0] == phase for k in expected_counts
        ):
            raise ValueError("use either aggregate probe or per-check expectations for a phase")
    phase_durations = phase_durations or {}
    if any(not isinstance(k, str) or isinstance(v, bool)
           or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
           for k, v in phase_durations.items()):
        raise ValueError("phase durations must map names to positive seconds")
    overall_acc = {kind: _Accumulator(kind) for kind in ("request", "probe")}
    phase_acc = {}
    phase_observed = Counter()
    check_acc = {}
    check_observed = Counter()
    for i, record in enumerate(records, 1):
        validate_record(record, i)
        phase = record.get("phase", "unspecified")
        phase_acc.setdefault(phase, {
            kind: _Accumulator(kind) for kind in ("request", "probe")
        })
        overall_acc[record["kind"]].add(record)
        phase_acc[phase][record["kind"]].add(record)
        if record.get("expected", True):
            phase_observed[(phase, record["kind"])] += 1
            if record["kind"] == "probe" and record.get("check"):
                check = record["check"]
                check_observed[(phase, check)] += 1
                check_acc.setdefault(check, {}).setdefault(
                    phase, _Accumulator("probe")
                ).add(record)
    for key, expected in expected_counts.items():
        if len(key) == 2:
            phase, kind = key
            observed_count = phase_observed[(phase, kind)]
        else:
            phase, _, check = key
            observed_count = check_observed[(phase, check)]
        if expected < observed_count:
            raise ValueError(f"expected count smaller than observed rows for {key}")

    overall = {}
    for kind in ("request", "probe"):
        all_missing = 0
        for key, expected in expected_counts.items():
            expected_kind = key[1]
            if expected_kind == kind:
                observed_count = (
                    phase_observed[(key[0], kind)]
                    if len(key) == 2
                    else check_observed[(key[0], key[2])]
                )
                all_missing += expected - observed_count
        overall[kind] = overall_acc[kind].summary(
            expected_count=overall_acc[kind].observed + all_missing
            if all_missing or kind in overall_acc else None,
            duration_seconds=duration_seconds,
            offered_rate=None,
        )

    phases = {}
    phase_names = sorted(set(phase_acc) | {key[0] for key in expected_counts})
    for phase in phase_names:
        phases[phase] = {}
        for kind in ("request", "probe"):
            acc = phase_acc.get(phase, {}).get(kind, _Accumulator(kind))
            phase_expected = expected_counts.get((phase, kind))
            if kind == "probe":
                check_expected = [
                    (key[2], count) for key, count in expected_counts.items()
                    if len(key) == 3 and key[0] == phase
                ]
                if check_expected:
                    phase_expected = sum(count for _, count in check_expected)
            phases[phase][kind] = acc.summary(
                phase_expected,
                phase_durations.get(phase, duration_seconds),
                offered_rate if kind == "request" and phase == "normal" else None,
            )

    checks_by_name = {}
    for check, by_phase in check_acc.items():
        checks_by_name[check] = {}
        for phase, acc in by_phase.items():
            expected = expected_counts.get((phase, "probe", check))
            checks_by_name[check][phase] = acc.summary(
                expected,
                phase_durations.get(phase, duration_seconds),
            )
    for key, expected in expected_counts.items():
        if len(key) == 3:
            phase, _, check = key
            acc = check_acc.get(check, {}).get(phase, _Accumulator("probe"))
            checks_by_name.setdefault(check, {})[phase] = acc.summary(
                expected,
                phase_durations.get(phase, duration_seconds),
            )

    normal = phases.get("normal", {}).get("request", {})
    availability = normal.get("good_ratio")
    p99 = normal.get("response_p99_ms")
    throughput_ratio = normal.get("accepted_throughput_over_offered")
    checks = {
        "availability_at_least_99_9_percent":
            availability is not None and availability >= AVAILABILITY_TARGET,
        "good_request_p99_at_most_10ms":
            p99 is not None and p99 <= REQUEST_P99_LIMIT_NS / 1_000_000,
        "accepted_throughput_at_least_99_percent_offered":
            throughput_ratio is not None and throughput_ratio >= THROUGHPUT_TARGET,
        "zero_errors_or_generator_drops":
            normal.get("execution_errors", 0) == 0
            and normal.get("generator_drops", 0) == 0,
    }
    budget_seconds = (
        (1.0 - AVAILABILITY_TARGET) * SLO_WINDOW_DAYS * 24 * 60 * 60
    )
    return {
        "schema_version": 1,
        "interpretation": {
            "status": "empirical test-window report; not production SLO/SLA evidence",
            "request_good_event": "outcome=ok and scheduled-to-response <=50ms",
            "probe_good_event": "ok=true; probe latency is reported separately",
            "missing_expected_records": "counted as bad; per-phase attribution requires phase expectations; probe check expectations preserve check type",
            "quantile": "floor-index empirical quantile matching bench/open_loop.py",
            "confidence": "No independent-sample confidence interval is reported; temporal and common-host failures correlate samples.",
        },
        "thresholds": {
            "normal_availability_target": AVAILABILITY_TARGET,
            "normal_request_p99_ms": REQUEST_P99_LIMIT_NS / 1_000_000,
            "normal_accepted_throughput_over_offered": THROUGHPUT_TARGET,
            "normal_zero_errors_or_generator_drops": True,
            "monthly_budget_arithmetic_only": {
                "window_days": SLO_WINDOW_DAYS,
                "target": AVAILABILITY_TARGET,
                "error_budget_seconds": budget_seconds,
                "error_budget_minutes": budget_seconds / 60,
                "warning": "30-day budget arithmetic only; this experiment does not measure a month.",
            },
        },
        "overall": overall,
        "phases": phases,
        "probe_checks": checks_by_name,
        "normal_experiment_checks": checks,
        "normal_experiment_pass": bool(checks) and all(checks.values()),
    }


def _parse_phase_expected(items):
    result = {}
    for item in items:
        try:
            parts = item.split(":")
            if len(parts) == 3:
                phase, kind, count = parts
                key = (phase, kind)
            elif len(parts) == 4 and parts[1] == "probe":
                phase, kind, check, count = parts
                if not check:
                    raise ValueError
                key = (phase, kind, check)
            else:
                raise ValueError
            if not phase or kind not in ("request", "probe"):
                raise ValueError
            if len(parts) == 4 and kind != "probe":
                raise ValueError
            number = int(count)
            if number < 0:
                raise ValueError
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "expected PHASE:request|probe:COUNT or PHASE:probe:CHECK:COUNT"
            ) from exc
        if key in result:
            raise argparse.ArgumentTypeError(f"duplicate phase expectation: {phase}/{kind}")
        result[key] = number
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="JSONL samples (default: stdin)")
    parser.add_argument("--output", help="write JSON report to this path (default: stdout)")
    parser.add_argument("--duration-seconds", type=float, help="measured window duration; preferred to inferred schedule span")
    parser.add_argument("--offered-rate", type=float, help="single offered request rate for the normal phase")
    parser.add_argument(
        "--phase-expected", action="append", default=[],
        metavar="PHASE:KIND[:CHECK]:COUNT",
        help="expected request/probe slots, including absent records; repeatable",
    )
    parser.add_argument(
        "--phase-duration", action="append", default=[], metavar="PHASE=SECONDS",
        help="measured phase duration; repeatable",
    )
    args = parser.parse_args(argv)
    try:
        expectations = _parse_phase_expected(args.phase_expected)
        phase_durations = {}
        for item in args.phase_duration:
            try:
                phase, seconds = item.split("=", 1)
                seconds = float(seconds)
                if not phase or not math.isfinite(seconds) or seconds <= 0 or phase in phase_durations:
                    raise ValueError
                phase_durations[phase] = seconds
            except ValueError as exc:
                raise ValueError("phase duration must be unique PHASE=positive-seconds") from exc
        if args.input == "-":
            stream, close = sys.stdin, False
        else:
            stream, close = open(args.input, encoding="utf-8"), True
        def records():
            try:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"line {line_number}: invalid JSON: {exc.msg}"
                        ) from exc
            finally:
                if close:
                    stream.close()
        report = analyze(
            records(),
            expected_counts=expectations,
            duration_seconds=args.duration_seconds,
            offered_rate=args.offered_rate,
            phase_durations=phase_durations,
        )
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
