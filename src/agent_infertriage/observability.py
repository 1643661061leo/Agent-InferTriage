"""Evidence capabilities are independent; metrics never stand in for request spans."""

from typing import Any

from agent_infertriage.models import EvidenceSet, Metric, Span, Usage, Workload


def capabilities(
    evidence: EvidenceSet,
    workload: Workload,
    spans: list[Span],
    graph: dict[str, Any],
) -> dict[str, Any]:
    levels = {s.observation_level for s in spans if s.start_ns is not None and s.end_ns is not None}
    client = "L0" in levels or any(
        s.observation_level is None
        and s.operation in ("llm", "chat", "generate", "inference", "model")
        and s.start_ns is not None
        and s.end_ns is not None
        for s in spans
    )
    cross_layer = (
        client
        and "L2" in levels
        and "L3" in levels
        and graph["status"] == "qualified"
        and workload.cross_layer_links_verified
        and workload.clock_alignment_verified
    )
    observed = {
        "L0": client,
        "L1": bool(evidence.service_series),
        "L2": "L2" in levels,
        "L3": "L3" in levels,
        "L4": cross_layer,
    }
    requirements = {
        "L0": "Instrument client calls/import timed client spans; no universal interception",
        "L1": "Attach existing Prometheus snapshots/query_range data; service scope only",
        "L2": "Import request-associated server spans explicitly labeled L2",
        "L3": "Import device profile spans explicitly labeled L3; no profiler is run automatically",
        "L4": "Qualified DAG plus verified cross-layer IDs and clock alignment, L0/L2/L3 evidence",
    }
    return {
        level: {
            "status": "observed" if present else "unknown",
            "requirement": requirements[level],
            "meaning": "Available evidence, not complete coverage of all phases or requests",
            "scope": "service" if level == "L1" else workload.workload_id,
        }
        for level, present in observed.items()
    }


def request_timings(spans: list[Span], usages: list[Usage]) -> list[dict[str, Any]]:
    results = []
    for usage in usages:
        owners = [s for s in spans if s.span_id == usage.span_id]
        related = [
            s
            for s in spans
            if s.request_id == usage.request_id
            and (
                s.attempt_id == usage.attempt_id or s.attempt_id is None and usage.attempt_id == "0"
            )
        ]
        client = [s for s in owners if s.observation_level in (None, "L0")]
        latency = Metric(unit="ns", reason="No unique client call span")
        if len(client) == 1 and client[0].start_ns is not None and client[0].end_ns is not None:
            latency = Metric(
                value=client[0].end_ns - client[0].start_ns,
                unit="ns",
                status="reported",
                method="client_monotonic_span",
                scope="L0",
                source_refs=client[0].source_refs,
                reason="Includes all client-visible waiting; not server compute time",
            )
        phases = {}
        for phase in ("queue", "prefill", "decode"):
            matching = [s for s in related if s.operation == phase and s.observation_level == "L2"]
            intervals = [
                (s.start_ns, s.end_ns)
                for s in matching
                if s.start_ns is not None and s.end_ns is not None
            ]
            known = (
                bool(matching)
                and len(intervals) == len(matching)
                and (len({s.clock_domain for s in matching}) == 1)
            )
            active = 0
            previous_end: int | None = None
            for start, end in sorted(intervals):
                active += max(
                    0, end - max(start, previous_end if previous_end is not None else start)
                )
                previous_end = max(end, previous_end if previous_end is not None else end)
            phases[phase] = Metric(
                value=active if known else None,
                unit="ns",
                status="derived" if known else "unknown",
                scope="L2",
                method="union_of_explicit_same_clock_phase_occurrences",
                source_refs=[r for s in matching for r in s.source_refs],
                reason=""
                if known
                else "No complete same-clock request phase; no internal split inferred",
            ).model_dump(mode="json") | {
                "occurrences": [
                    {
                        "span_id": s.span_id,
                        "engine_iteration_id": s.engine_iteration_id,
                        "start_ns": s.start_ns,
                        "end_ns": s.end_ns,
                    }
                    for s in matching
                ]
            }
        ttft = owners[0].attributes.get("client_ttft_ns") if len(owners) == 1 else None
        valid_ttft = isinstance(ttft, int) and not isinstance(ttft, bool) and ttft >= 0
        results.append(
            {
                "request_id": usage.request_id,
                "attempt_id": usage.attempt_id,
                "client_visible_latency": latency.model_dump(mode="json"),
                "client_ttft": Metric(
                    value=ttft if valid_ttft else None,
                    unit="ns",
                    status="reported" if valid_ttft else "unknown",
                    scope="L0",
                    source_refs=owners[0].source_refs if owners else [],
                    reason="First token event at client; not server TTFT"
                    if valid_ttft
                    else "No explicit client first-token event",
                ).model_dump(mode="json"),
                "server_phases": phases,
                "kernel": {
                    "status": "observed"
                    if any(s.observation_level == "L3" for s in related)
                    else "unknown",
                    "span_ids": [s.span_id for s in related if s.observation_level == "L3"],
                    "reason": "Kernel durations may overlap and are not exclusive request latency",
                },
            }
        )
    return results
