"""L0 observed wall-time partition and L1 service signals, not invented causal attribution."""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from agent_infertriage.models import Span, Usage, Workload

STEP_KINDS = {
    "agent_init",
    "document_read",
    "prompt_build",
    "agent_decision",
    "tool_dispatch",
    "health_check",
    "readiness_wait",
    "poll_wait",
    "service_launch",
    "inference",
    "response_parse",
    "handoff_serialize",
    "handoff_transfer",
    "handoff_deserialize",
    "retry_backoff",
    "metrics_capture",
    "other",
}


def kind(span: Span) -> str:
    explicit = span.attributes.get("step_kind")
    if isinstance(explicit, str) and explicit in STEP_KINDS:
        return str(explicit)
    if span.operation == "service_startup":
        return (
            "readiness_wait"
            if span.attributes.get("startup_phase") == "readiness"
            else "service_launch"
        )
    if span.operation in ("llm", "chat", "model", "inference", "generate"):
        return "inference"
    return "uncategorized"


def workflow_timing(workload: Workload, spans: list[Span], usages: list[Usage]) -> dict[str, Any]:
    start, end = workload.start_ns, workload.end_ns
    valid = [
        s
        for s in spans
        if s.start_ns is not None
        and s.end_ns is not None
        and s.clock_domain == workload.clock_domain
        and s.observation_level in (None, "L0")
    ]
    rows = []
    for span in valid:
        owned = [u for u in usages if u.span_id == span.span_id]
        rows.append(
            {
                "span_id": span.span_id,
                "parent_id": span.parent_id,
                "name": span.name,
                "kind": kind(span),
                "start_ns": span.start_ns,
                "end_ns": span.end_ns,
                "inclusive_ns": (span.end_ns or 0) - (span.start_ns or 0),
                "agent_id": span.agent_id,
                "round_id": span.round_id,
                "request_id": span.request_id,
                "attempt_id": span.attempt_id,
                "attributes": span.attributes,
                "source_refs": span.source_refs,
                "input_tokens": sum(u.input_tokens or 0 for u in owned)
                if owned and all(u.input_tokens is not None for u in owned)
                else None,
                "output_tokens": sum(u.output_tokens or 0 for u in owned)
                if owned and all(u.output_tokens is not None for u in owned)
                else None,
                "pure_thinking_ns": None,
            }
        )
    rows.sort(key=lambda r: (r["start_ns"], r["span_id"]))
    result: dict[str, Any] = {
        "boundaries": {
            "start": workload.start_boundary,
            "end": workload.end_boundary,
            "completion_status": workload.completion_status,
            "start_ns": start,
            "end_ns": end,
        },
        "total_ns": end - start if start is not None and end is not None else None,
        "steps": rows,
        "partition": [],
        "phase_envelopes": [],
        "contributors": [],
        "candidates": [],
        "excluded_span_count": len(spans) - len(valid),
        "excluded_span_ids": [s.span_id for s in spans if s not in valid],
        "semantics": "Most-specific observed wall intervals; not CPU self time or causal savings. "
        "Uninstrumented gaps are not handoffs. Model API wait is not pure thinking.",
    }
    if start is None or end is None:
        return result
    # Sweep boundaries, retaining parallel overlap instead of serializing sibling work.
    events: dict[int, list[tuple[bool, str]]] = defaultdict(list)
    by_id = {s.span_id: s for s in valid}
    for span in valid:
        a, b = max(start, span.start_ns or 0), min(end, span.end_ns or 0)
        if a < b:
            events[a].append((True, span.span_id))
            events[b].append((False, span.span_id))
    points = sorted({start, end, *events})
    active: set[str] = set()
    totals: dict[str, int] = defaultdict(int)
    refs: dict[str, set[str]] = defaultdict(set)
    for a, b in zip(points, points[1:], strict=False):
        for opening, sid in events.get(a, []):
            if opening:
                active.add(sid)
            else:
                active.discard(sid)
        ancestors: set[str] = set()
        for sid in active:
            parent = by_id[sid].parent_id
            visited: set[str] = set()
            while parent in by_id and parent not in visited:
                visited.add(parent)
                ancestors.add(parent)
                parent = by_id[parent].parent_id
        leaves = active - ancestors
        category = (
            kind(by_id[next(iter(leaves))])
            if len(leaves) == 1
            else ("parallel_overlap" if leaves else "unattributed")
        )
        interval = {
            "start_ns": a,
            "end_ns": b,
            "duration_ns": b - a,
            "kind": category,
            "span_ids": sorted(leaves),
        }
        result["partition"].append(interval)
        totals[category] += b - a
        refs[category].update(r for sid in leaves for r in by_id[sid].source_refs)
    total = end - start
    for category, duration in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0])):
        result["contributors"].append(
            {
                "kind": category,
                "observed_ns": duration,
                "wall_fraction": duration / total if total else None,
                "source_refs": sorted(refs[category]),
                "benefit": {
                    "status": "unknown",
                    "reason": "Requires qualified dependencies and a validated intervention",
                },
            }
        )
    result["reconciled"] = sum(totals.values()) == total
    result["observed_fraction"] = (total - totals.get("unattributed", 0)) / total if total else None
    result["out_of_bounds_span_ids"] = [
        s.span_id for s in valid if (s.start_ns or 0) < start or (s.end_ns or 0) > end
    ]
    result["phase_envelopes"] = [
        {
            "span_id": row["span_id"],
            "kind": row["kind"],
            "inclusive_ns": row["inclusive_ns"],
            "source_refs": row["source_refs"],
            "meaning": "Inclusive phase envelope; overlaps its child steps, never add to partition",
        }
        for row in rows
        if row["kind"] in ("readiness_wait", "service_launch")
    ]
    for envelope in result["phase_envelopes"]:
        result["candidates"].append(
            {
                "category": envelope["kind"] + "_envelope",
                "status": "observed_time_not_cause",
                "observed_ns": envelope["inclusive_ns"],
                "source_refs": envelope["source_refs"],
                "next_check": "Inspect lifecycle/readiness detection; internal startup unknown.",
                "benefit": {"status": "unknown"},
                "additive": False,
            }
        )
    checks = {
        "service_launch": "Inspect process launch and lifecycle; internal load substages unknown.",
        "readiness_wait": "Separate readiness waiting, probe I/O and polling detection delay.",
        "poll_wait": "Polling sleep is intentional waiting, not measured model loading or handoff.",
        "agent_decision": "Compare context, API wait and parsing; pure thinking remains unknown.",
        "inference": "Compare input/output, TTFT and wall time; internal phases need L2.",
        "document_read": "Inspect file volume and repeated reads; repetition may be inexpensive.",
        "handoff_serialize": "Inspect packing and payload size, not receiver model waiting.",
        "handoff_transfer": "Inspect measured transport/ack wait; gaps do not prove handoff delay.",
        "handoff_deserialize": "Inspect parsing/validation and payload size at receiver.",
        "retry_backoff": "Inspect failed attempts, retry count and intentional backoff.",
        "unattributed": "Instrument missing work; do not label it idle, handoff or network time.",
        "parallel_overlap": "Inspect parallel branches before estimating removable wall time.",
    }
    for item in result["contributors"]:
        result["candidates"].append(
            {
                "category": item["kind"],
                "status": "observed_time_not_cause",
                "observed_ns": item["observed_ns"],
                "source_refs": item["source_refs"],
                "next_check": checks.get(
                    item["kind"], "Inspect this explicitly timed step and its evidence."
                ),
                "benefit": item["benefit"],
            }
        )
    for category in (
        "handoff_serialize",
        "handoff_transfer",
        "handoff_deserialize",
        "retry_backoff",
    ):
        if not any(kind(s) == category for s in valid):
            result["candidates"].append(
                {
                    "category": category,
                    "status": "unknown",
                    "observed_ns": None,
                    "reason": "No explicitly timed occurrence",
                }
            )
    return result


def service_windows(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe counter windows and gauge samples. Never allocate them to requests."""
    result = []
    for item in series:
        name = item["name"]
        if (
            not any(
                part in name
                for part in (
                    "prefix_cache",
                    "requests_waiting",
                    "requests_running",
                    "preemption",
                    "kv_cache_usage",
                    "request_queue_time",
                    "request_prefill_time",
                    "request_decode_time",
                )
            )
            or name.endswith("_created")
            or "_bucket" in name
        ):
            continue
        samples = item["samples"]
        counter = item["metric_type"] == "counter" or (
            item["metric_type"] in ("histogram", "summary") and name.endswith(("_sum", "_count"))
        )
        valid = (
            item["metadata_consistent"]
            and len(samples) >= 2
            and all(s["timestamp_ns"] is not None and s["value"] is not None for s in samples)
            and all(
                b["timestamp_ns"] > a["timestamp_ns"]
                for a, b in zip(samples, samples[1:], strict=False)
            )
        )
        reset = (
            any(
                Decimal(b["value"]) < Decimal(a["value"])
                for a, b in zip(samples, samples[1:], strict=False)
                if a["value"] is not None and b["value"] is not None
            )
            if counter
            else False
        )
        delta = (
            str(Decimal(samples[-1]["value"]) - Decimal(samples[0]["value"]))
            if (counter and valid and not reset)
            else None
        )
        result.append(
            {
                "name": name,
                "labels": item["labels"],
                "endpoint_id": item["endpoint_id"],
                "scope": "service_window_not_request",
                "source_refs": item["source_refs"],
                "time_window_ns": item["time_window_ns"],
                "sample_count": len(samples),
                "status": "derived"
                if delta is not None
                else "observed_samples"
                if not counter
                else "unknown",
                "delta": delta,
                "counter_intervals": [
                    {
                        "start_ns": a["timestamp_ns"],
                        "end_ns": b["timestamp_ns"],
                        "delta": str(Decimal(b["value"]) - Decimal(a["value"])),
                    }
                    for a, b in zip(samples, samples[1:], strict=False)
                ]
                if counter and valid and not reset
                else [],
                "minimum": item["minimum"],
                "maximum": item["maximum"],
                "counter_reset": reset,
                "semantic_version": item["declared_versions"],
                "limitation": (
                    "Sparse samples can miss transient queues/resets; verify engine metric units. "
                    "No request causality or billing inferred."
                ),
            }
        )
    return result
