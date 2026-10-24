"""Fixed-DAG counterfactuals. Containment is deliberately never a dependency."""

from collections import defaultdict, deque
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from agent_infertriage.models import Activity, Dependency, EvidenceSet, Workload


def solve(
    activities: list[Activity],
    dependencies: list[Dependency],
    weights: dict[str, int] | None = None,
) -> dict[str, Any]:
    nodes = {a.activity_id: a for a in activities}
    if len(nodes) != len(activities):
        raise ValueError("Duplicate execution activity ID")
    incoming: dict[str, list[tuple[str, int]]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    degree = {node: 0 for node in nodes}
    edges: set[tuple[str, str]] = set()
    for edge in dependencies:
        if edge.from_id not in nodes or edge.to_id not in nodes:
            raise ValueError("Execution graph references missing activities")
        pair = (edge.from_id, edge.to_id)
        if pair in edges:
            raise ValueError("Duplicate execution dependency")
        edges.add(pair)
        incoming[edge.to_id].append((edge.from_id, edge.min_lag_ns))
        outgoing[edge.from_id].append(edge.to_id)
        degree[edge.to_id] += 1
    ready = deque(sorted(node for node, count in degree.items() if count == 0))
    finishes: dict[str, int] = {}
    previous: dict[str, str | None] = {}
    path_counts: dict[str, int] = {}
    while ready:
        node = ready.popleft()
        activity = nodes[node]
        candidates = [(finishes[p] + lag, p) for p, lag in incoming[node]]
        maximum = max((time for time, _ in candidates), default=0)
        start = max(activity.release_ns, maximum)
        matching = sorted(p for time, p in candidates if time == start)
        previous[node] = matching[0] if matching else None
        path_counts[node] = min(2, sum(path_counts[p] for p in matching)) if matching else 1
        weight = weights.get(node, activity.duration_ns) if weights else activity.duration_ns
        if weight < 0:
            raise ValueError("Negative activity weight")
        finishes[node] = start + weight
        for child in outgoing[node]:
            degree[child] -= 1
            if degree[child] == 0:
                ready.append(child)
    if len(finishes) != len(nodes):
        raise ValueError("Cycle in execution graph")
    total = max(finishes.values(), default=0)
    sinks = sorted(n for n in nodes if not outgoing[n] and finishes[n] == total)
    path: list[str] = []
    cursor: str | None = sinks[0] if sinks else None
    while cursor is not None:
        path.append(cursor)
        cursor = previous[cursor]
    return {
        "duration_ns": total,
        "critical_path": list(reversed(path)),
        "multiple_critical_paths": sum(path_counts[n] for n in sinks) > 1,
    }


def qualify(evidence: EvidenceSet, workload: Workload) -> dict[str, Any]:
    activities = [a for a in evidence.activities if a.workload_id == workload.workload_id]
    dependencies = [e for e in evidence.dependencies if e.workload_id == workload.workload_id]
    reasons: list[str] = []
    if not activities:
        reasons.append(
            "No explicit non-overlapping activity model; span parents are not dependencies"
        )
    if not workload.graph_complete:
        reasons.append(
            "Source has not declared complete execution dependencies and waiting coverage"
        )
    if (
        workload.graph_provenance == "inferred"
        or any(a.provenance == "inferred" for a in activities)
        or any(e.provenance == "inferred" for e in dependencies)
    ):
        reasons.append("Inferred dependencies/activities cannot establish a precise global bound")
    execution_keys = [a.execution_key or a.activity_id for a in activities]
    if len(set(execution_keys)) != len(execution_keys):
        reasons.append("Duplicated physical execution in activity model")
    clocks = {c.clock_id: c for c in evidence.manifest.clock_domains}
    for activity in activities:
        clock = clocks.get(activity.clock_domain)
        if not clock or (
            activity.clock_domain != workload.clock_domain
            and clock.aligned_to != workload.clock_domain
        ):
            reasons.append("Activity clocks are not aligned to workload clock")
            break
    baseline: dict[str, Any] = {}
    try:
        baseline = solve(activities, dependencies)
    except ValueError as exc:
        reasons.append(str(exc))
    observed: int | None = None
    residual: int | None = None
    tolerance: int | None = None
    if workload.start_ns is None or workload.end_ns is None:
        reasons.append("Observed workload duration unavailable")
    elif baseline:
        observed = workload.end_ns - workload.start_ns
        uncertainty = sum(c.uncertainty_ns for c in clocks.values())
        tolerance = max(1_000_000, observed // 100) + uncertainty
        residual = abs(baseline["duration_ns"] - observed)
        if residual > tolerance:
            reasons.append("Execution model does not reproduce observed workload duration")
    return {
        "status": "qualified" if not reasons else "unknown",
        "reasons": reasons,
        "scope": workload.workload_id,
        "baseline": baseline,
        "observed_ns": observed,
        "baseline_error_ns": residual,
        "tolerance_ns": tolerance,
        "assumptions": [
            "Fixed explicit dependencies and activity partition",
            "Other activity durations and external release constraints unchanged",
            "No resimulation of resource scheduling or agent behavior",
        ],
        "source_refs": list(dict.fromkeys(ref for a in activities for ref in a.source_refs)),
    }


def scenario(
    evidence: EvidenceSet,
    workload_id: str,
    activity_ids: list[str],
    speedup: str | None = None,
) -> dict[str, Any]:
    workload = next((w for w in evidence.manifest.workloads if w.workload_id == workload_id), None)
    if workload is None:
        raise ValueError("Unknown workload scope")
    graph = qualify(evidence, workload)
    if graph["status"] != "qualified":
        return graph | {"saved_ns": None}
    nodes = [a for a in evidence.activities if a.workload_id == workload_id]
    edges = [d for d in evidence.dependencies if d.workload_id == workload_id]
    selected = set(activity_ids)
    if not selected or not selected.issubset({a.activity_id for a in nodes}):
        raise ValueError("Scenario must select existing activities in the requested workload")
    try:
        factor = Decimal(speedup) if speedup is not None else None
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Speedup must be a decimal number") from exc
    if factor is not None and (not factor.is_finite() or factor < 1):
        raise ValueError("Speedup must be finite and at least 1")
    weights = {
        a.activity_id: (
            int((Decimal(a.duration_ns) / factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            if factor
            else 0
        )
        for a in nodes
        if a.activity_id in selected
    }
    result = solve(nodes, edges, weights)
    before = graph["baseline"]["duration_ns"]
    after = result["duration_ns"]
    return graph | {
        "selected_activity_ids": sorted(selected),
        "scenario": result,
        "saved_ns": before - after,
        "reduction_fraction": (before - after) / before if before else None,
        "speedup": before / after if after else None,
        "all_modeled_duration_removed": after == 0,
    }
