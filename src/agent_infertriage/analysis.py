"""Shared deterministic analysis for offline and agent modes."""

import hashlib
import json
import math
import re
from collections import defaultdict
from typing import Any

from agent_infertriage.accounting import (
    cache_summary,
    cost_summary,
    deduplicate,
    load_prices,
    token_summary,
)
from agent_infertriage.evidence import validate
from agent_infertriage.graph import qualify, scenario
from agent_infertriage.models import (
    STAGES,
    AnalysisConfig,
    AnalysisResult,
    EvidenceSet,
    Finding,
    Metric,
    Span,
    Usage,
)
from agent_infertriage.observability import capabilities, request_timings
from agent_infertriage.prometheus import summarize as summarize_service_metrics
from agent_infertriage.workflow import service_windows, workflow_timing


def interval_union(intervals: list[tuple[int, int]]) -> int:
    total = 0
    end: int | None = None
    for start, stop in sorted(intervals):
        if stop < start:
            raise ValueError("Negative interval")
        total += max(0, stop - max(start, end if end is not None else start))
        end = max(stop, end if end is not None else stop)
    return total


def span_time(span: Span) -> int | None:
    if span.start_ns is None or span.end_ns is None:
        return None
    return span.end_ns - span.start_ns


def self_time(span: Span, spans: list[Span]) -> Metric:
    duration = span_time(span)
    children = [s for s in spans if s.parent_id == span.span_id]
    reason = "Source has not declared complete child instrumentation"
    if duration is None:
        reason = "Missing span boundaries"
    elif span.attributes.get("children_complete") is True:
        intervals: list[tuple[int, int]] = []
        for child in children:
            if (
                child.clock_domain != span.clock_domain
                or child.start_ns is None
                or child.end_ns is None
                or span.start_ns is None
                or span.end_ns is None
                or child.start_ns < span.start_ns
                or child.end_ns > span.end_ns
            ):
                return Metric(unit="ns", reason="Unaligned/incomplete/out-of-bounds child")
            intervals.append((child.start_ns, child.end_ns))
        return Metric(
            value=duration - interval_union(intervals),
            unit="ns",
            status="derived",
            method="parent_minus_union_of_complete_children",
            source_refs=span.source_refs,
        )
    return Metric(unit="ns", reason=reason)


def timing_summary(spans: list[Span], evidence: EvidenceSet) -> dict[str, Any]:
    known = [s for s in spans if span_time(s) is not None]
    cumulative = sum(span_time(s) or 0 for s in known)
    refs = sorted({r for s in spans for r in s.source_refs})
    missing = len(spans) - len(known)
    total = Metric(
        value=cumulative if spans and not missing else None,
        unit="ns",
        status="derived" if spans and not missing else "unknown",
        known_subtotal=cumulative,
        unknown_count=missing,
        method="sum_inclusive_span_durations_may_overlap",
        source_refs=refs,
        reason="May include nested/parallel durations; not wall time",
    )
    clocks = {c.clock_id: c for c in evidence.manifest.clock_domains}
    domains = {s.clock_domain for s in known}
    aligned: list[tuple[int, int]] = []
    targets: set[str] = set()
    for span in known:
        clock = clocks[span.clock_domain]
        targets.add(clock.aligned_to or clock.clock_id)
        if span.start_ns is not None and span.end_ns is not None:
            aligned.append((span.start_ns + clock.offset_ns, span.end_ns + clock.offset_ns))
    union_ok = bool(spans) and not missing and (len(domains) == 1 or len(targets) == 1)
    union = Metric(
        value=interval_union(aligned) if union_ok else None,
        unit="ns",
        status="derived" if union_ok else "unknown",
        method="aligned_interval_union",
        source_refs=refs,
        reason="" if union_ok else "Incomplete/unaligned span intervals",
    )
    durations = sorted(span_time(s) or 0 for s in known)
    return {
        "span_count": len(spans),
        "cumulative_inclusive_ns": total.model_dump(mode="json"),
        "active_union_ns": union.model_dump(mode="json"),
        "distribution": {
            "sample_count": len(durations),
            "method": "nearest_rank",
            "p50_ns": durations[math.ceil(len(durations) * 0.5) - 1] if durations else None,
            "p95_ns": durations[math.ceil(len(durations) * 0.95) - 1] if durations else None,
        },
    }


def usages_for_spans(usages: list[Usage], spans: list[Span], all_spans: list[Span]) -> list[Usage]:
    selected = {s.span_id for s in spans}
    # Inclusive ancestor attribution is a view; usage ownership remains on requests.
    children: dict[str, list[str]] = defaultdict(list)
    for span in all_spans:
        if span.parent_id:
            children[span.parent_id].append(span.span_id)
    pending = list(selected)
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in selected:
                selected.add(child)
                pending.append(child)
    request_attempts = {
        (r, s.attempt_id or "0")
        for s in all_spans
        if s.span_id in selected
        for r in s.request_refs + ([s.request_id] if s.request_id else [])
    }
    return [
        u
        for u in usages
        if u.span_id in selected or (u.request_id, u.attempt_id) in request_attempts
    ]


def rules(
    evidence: EvidenceSet,
    workload_id: str,
    usages: list[Usage],
    config: AnalysisConfig,
    metrics: dict[str, Metric],
) -> list[Finding]:
    findings: list[Finding] = []
    spans = [s for s in evidence.spans if s.workload_id == workload_id]

    def add(
        rule: str,
        title: str,
        scopes: list[str],
        refs: list[str],
        layers: list[str],
        next_check: str,
        unknowns: list[str] | None = None,
    ) -> Finding:
        item = Finding(
            finding_id=f"{workload_id}/{rule}/{len(findings)}",
            rule_id=rule,
            title=title,
            scope_refs=scopes,
            evidence_refs=sorted(set(refs)),
            owner_layers=layers,
            observation_levels=sorted(
                {
                    s.observation_level or "L0"
                    for s in spans
                    if s.span_id in scopes or s.record_id in scopes
                }
            )
            or ["L0"],
            confidence_reason="Deterministic pattern; relevance and causal benefit need validation",
            unknowns=unknowns or [],
            next_check=next_check,
        )
        findings.append(item)
        return item

    groups: dict[str, list[Usage]] = defaultdict(list)
    by_span = {s.span_id: s for s in spans}
    for usage in usages:
        groups[usage.agent_id or "unknown"].append(usage)
    for agent, group in sorted(groups.items()):
        if any(u.span_id not in by_span or by_span[u.span_id].start_ns is None for u in group):
            continue  # Request names and arbitrary IDs do not establish chronological order.
        group.sort(
            key=lambda u: (
                by_span[u.span_id].start_ns
                if u.span_id in by_span and by_span[u.span_id].start_ns is not None
                else 0,
                u.request_id,
                u.attempt_id,
            )
        )
        known = [u for u in group if u.input_tokens is not None]
        if len(known) >= config.context_min_calls:
            first, last = known[0].input_tokens, known[-1].input_tokens
            assert first is not None and last is not None
            if (
                last - first >= config.context_growth_tokens
                and last >= max(1, first) * config.context_growth_ratio
            ):
                item = add(
                    "context_growth",
                    "Input context grows across agent calls",
                    [u.record_id for u in known],
                    [r for u in known for r in u.source_refs],
                    ["orchestration", "prompt"],
                    "Inspect content sources and cached input before removing context.",
                    ["Growth alone does not establish unnecessary context or latency savings"],
                )
                key = f"{workload_id}/context_growth/{agent}/delta_tokens"
                metrics[key] = Metric(
                    value=last - first,
                    unit="tokens",
                    status="derived",
                    source_refs=item.evidence_refs,
                    method="last_minus_first_input",
                )
                item.metric_refs.append(key)

    tool_groups: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        attrs = span.attributes
        if span.operation not in ("tool", "execute_tool") or "arguments" not in attrs:
            continue
        if attrs.get("polling") or attrs.get("required_review"):
            continue
        key = json.dumps(
            [
                span.agent_id,
                attrs.get("tool_name", span.name),
                attrs["arguments"],
                attrs.get("resource_version"),
            ],
            sort_keys=True,
            default=str,
        )
        tool_groups[key].append(span)
    for tool_group in tool_groups.values():
        if len(tool_group) < 2:
            continue
        refs = [r for s in tool_group for r in s.source_refs]
        item = add(
            "repeated_tool_call",
            "Repeated tool arguments require a freshness check",
            [s.span_id for s in tool_group],
            refs,
            ["orchestration"],
            "Confirm input version, read-only/idempotent semantics and necessity of refresh.",
            ["Repeated calls can be necessary; no automatic removal"],
        )
        removable = all(
            s.attributes.get("removal_safe") is True
            and s.attributes.get("read_only") is True
            and s.attributes.get("idempotent") is True
            and s.attributes.get("resource_version") is not None
            for s in tool_group
        )
        if removable:
            ordered = sorted(tool_group, key=lambda s: s.start_ns or 0)
            ids = [
                a.activity_id
                for a in evidence.activities
                if a.workload_id == workload_id
                and a.scope_span_id in {s.span_id for s in ordered[1:]}
            ]
            if ids:
                item.benefit = scenario(evidence, workload_id, ids)
        failed = [s for s in tool_group if s.attributes.get("status") in ("failed", "error")]
        if len(failed) >= config.retry_min_calls:
            add(
                "retry_no_progress",
                "Repeated failed tool calls",
                [s.span_id for s in failed],
                refs,
                ["orchestration"],
                "Check whether retries changed inputs or recovered transient state.",
            )

    contents = [c for c in evidence.contents if c.workload_id == workload_id]
    repeated: dict[str, list[Any]] = defaultdict(list)
    for block in contents:
        if block.role in ("tool", "tool_result") and (block.content_key or block.text):
            key = block.content_key or hashlib.sha256((block.text or "").encode()).hexdigest()
            repeated[key].append(block)
    for blocks in repeated.values():
        if len({b.request_id for b in blocks}) < 2:
            continue
        add(
            "tool_result_amplification",
            "Tool result is carried in multiple requests",
            [b.record_id for b in blocks],
            [r for b in blocks for r in b.source_refs],
            ["prompt", "cache"],
            "Check necessity and billed cache usage before trimming results.",
        )

    prompts: dict[str, list[Any]] = defaultdict(list)
    for block in contents:
        if block.role in ("system", "prompt"):
            prompts[block.request_id].append(block)
    if len(prompts) >= 3:
        texts = ["".join(b.text or "" for b in blocks) for blocks in prompts.values()]
        normalized = [
            re.sub(
                r"\b\d{4}-\d{2}-\d{2}[T ][\d:.+Z-]+|\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b",
                "<dynamic>",
                text,
            )
            for text in texts
        ]
        if len(set(texts)) > 1 and len(set(normalized)) == 1 and len(normalized[0]) >= 128:
            add(
                "prefix_instability",
                "Dynamic fields may split an otherwise stable prompt prefix",
                list(prompts),
                [r for blocks in prompts.values() for b in blocks for r in b.source_refs],
                ["prompt", "cache"],
                "Inspect exact token prefix and actual cache usage.",
                ["Character-level comparison, not verified tokenizer or server cache behavior"],
            )
        tokenized = []
        signatures: set[tuple[str | None, str | None]] = set()
        for blocks in prompts.values():
            if all(b.token_ids is not None and b.tokenizer_ref and b.template_ref for b in blocks):
                tokenized.append([token for b in blocks for token in b.token_ids])
                signatures.update((b.tokenizer_ref, b.template_ref) for b in blocks)
        if len(tokenized) == len(prompts) and len(signatures) == 1:
            common = 0
            for tokens in zip(*tokenized, strict=False):
                if len(set(tokens)) != 1:
                    break
                common += 1
            measured = [
                u for u in usages if u.request_id in prompts and u.cache_read_tokens is not None
            ]
            if common >= 256 and len(measured) >= 3:
                queries = sum(u.input_tokens or 0 for u in measured)
                hits = sum(u.cache_read_tokens or 0 for u in measured)
                if queries and hits / queries < 0.2:
                    add(
                        "prefix_reuse_gap",
                        "Stable token prefixes coexist with low measured reuse",
                        list(prompts),
                        [r for u in measured for r in u.source_refs],
                        ["cache", "prompt"],
                        "Check cache enablement, TTL, routing, eviction and sharing domain.",
                        ["Correlation does not prove a cache defect"],
                    )
    return findings


def analyze(evidence: EvidenceSet, config: AnalysisConfig | None = None) -> AnalysisResult:
    config = config or AnalysisConfig()
    warnings = validate(evidence)
    prices = load_prices(config.price_file)
    all_usages = deduplicate(evidence.usages)
    metrics: dict[str, Metric] = {}
    findings: list[Finding] = []
    workload_results: list[dict[str, Any]] = []
    index: dict[str, Any] = {"sources": list(evidence.sources), "records": {}}
    index["service_series"] = [
        {"name": s["name"], "scope": "service_aggregate", "source_refs": s["source_refs"]}
        for s in evidence.service_series
    ]
    service_metrics = summarize_service_metrics(evidence.service_series)
    for field in ("spans", "activities", "dependencies", "usages", "caches", "costs", "contents"):
        for record in getattr(evidence, field):
            index["records"][record.record_id] = record.source_refs
    for workload in evidence.manifest.workloads:
        wid = workload.workload_id
        spans = [s for s in evidence.spans if s.workload_id == wid]
        usages = [u for u in all_usages if u.workload_id == wid]
        # A timed model call without usage is an unknown call, not a zero-token call.
        owned = {(u.request_id, u.attempt_id) for u in usages}
        owned_spans = {u.span_id for u in usages}
        for span in spans:
            if span.operation not in ("chat", "generate", "inference", "llm", "model"):
                continue
            request_id = span.request_id or f"unreported:{span.span_id}"
            attempt_id = span.attempt_id or "0"
            if (request_id, attempt_id) in owned or span.span_id in owned_spans:
                continue
            usages.append(
                Usage(
                    record_id=f"missing-usage:{span.span_id}",
                    workload_id=wid,
                    request_id=request_id,
                    attempt_id=attempt_id,
                    span_id=span.span_id,
                    agent_id=span.agent_id,
                    round_id=span.round_id,
                    usage_status="unknown",
                    source_refs=span.source_refs,
                )
            )
            owned.add((request_id, attempt_id))
        costs = [c for c in evidence.costs if c.workload_id == wid]
        caches = [c for c in evidence.caches if c.workload_id == wid]
        summary = token_summary(usages, wid)
        for name, metric in summary.items():
            metrics[f"{wid}/tokens/{name}"] = metric
        wall = (
            workload.end_ns - workload.start_ns
            if workload.end_ns is not None and workload.start_ns is not None
            else None
        )
        metrics[f"{wid}/wall_time_ns"] = Metric(
            value=wall,
            unit="ns",
            status="reported" if wall is not None else "unknown",
            source_refs=workload.source_refs,
            reason="" if wall is not None else "Missing boundaries",
        )
        span_details = []
        children_by_parent: dict[str, list[Span]] = defaultdict(list)
        for span in spans:
            if span.parent_id:
                children_by_parent[span.parent_id].append(span)
        for span in spans:
            key = f"{wid}/span/{span.span_id}/elapsed_ns"
            value = span_time(span)
            metrics[key] = Metric(
                value=value,
                unit="ns",
                status="derived" if value is not None else "unknown",
                source_refs=span.source_refs,
            )
            span_details.append(
                span.model_dump(mode="json")
                | {
                    "elapsed_metric_ref": key,
                    "self_time_ns": self_time(
                        span, children_by_parent.get(span.span_id, [])
                    ).model_dump(mode="json"),
                }
            )
        coverage: dict[str, Any] = {}
        stages: dict[str, Any] = {}
        graph = qualify(evidence, workload)
        layer_bounds: list[dict[str, Any]] = []
        for stage in STAGES:
            matching = [s for s in spans if stage in s.stages]
            stage_usages = usages_for_spans(usages, matching, spans)
            complete = bool(matching) and all(span_time(s) is not None for s in matching)
            coverage[stage] = {
                "status": "observed" if complete else "partial" if matching else "unknown",
                "meaning": "Observed stage spans, not a guarantee of exhaustive instrumentation",
                "missing_evidence": []
                if complete
                else ["Stage timing not available/completely instrumented"],
                "collection_hint": "Import request-associated server/profile spans"
                if stage in ("scheduling", "kernel", "cache")
                else "Provide stage-specific spans and round IDs",
            }
            tokens = token_summary(stage_usages, f"{wid}/{stage}")
            stages[stage] = timing_summary(matching, evidence) | {
                "span_ids": [s.span_id for s in matching],
                "tokens": {k: v.model_dump(mode="json") for k, v in tokens.items()},
                "token_attribution": "inclusive view; not additive across layers",
                "independent_token_accounting": "not_applicable"
                if stage == "kernel"
                else "request_owned",
                "caches": cache_summary([c for c in caches if c.scope == stage]),
                "cost": cost_summary(
                    stage_usages,
                    [
                        c
                        for c in costs
                        if c.owner_event
                        in {f"request:{u.request_id}:{u.attempt_id}" for u in stage_usages}
                        or c.owner_event in {s.span_id for s in matching}
                    ],
                    prices,
                ),
            }
            activities = [
                a.activity_id
                for a in evidence.activities
                if a.workload_id == wid and stage in a.stages
            ]
            bound = (
                scenario(evidence, wid, activities)
                if activities
                else {
                    "status": "unknown",
                    "saved_ns": None,
                    "reasons": ["No separately modeled activities for this layer"],
                }
            )
            layer_bounds.append({"layer": stage, "benefit": bound})
        rounds = []
        group_keys = {(s.agent_id, s.round_id) for s in spans} | {
            (u.agent_id, u.round_id) for u in usages
        }
        for agent, rid in sorted(group_keys, key=lambda key: (key[0] or "", key[1] or "")):
            group = [s for s in spans if (s.agent_id, s.round_id) == (agent, rid)]
            group_usage = [u for u in usages if (u.agent_id, u.round_id) == (agent, rid)]
            agents_in_round = {s.agent_id for s in spans if s.round_id == rid} | {
                u.agent_id for u in usages if u.round_id == rid
            }
            rounds.append(
                {
                    "agent_id": agent,
                    "round_id": rid,
                    "round_provenance": "source" if rid else "unknown",
                    "span_ids": [s.span_id for s in group],
                    "timing": timing_summary(group, evidence),
                    "tokens": {
                        k: v.model_dump(mode="json") for k, v in token_summary(group_usage).items()
                    },
                    "caches": cache_summary(
                        [
                            c
                            for c in caches
                            if c.round_id == rid
                            and (
                                c.agent_id == agent
                                or c.agent_id is None
                                and len(agents_in_round) == 1
                            )
                        ]
                    ),
                    "cost": cost_summary(
                        group_usage,
                        [
                            c
                            for c in costs
                            if c.owner_event
                            in {f"request:{u.request_id}:{u.attempt_id}" for u in group_usage}
                            or c.owner_event in {s.span_id for s in group}
                        ],
                        prices,
                    ),
                }
            )
        local_findings = rules(evidence, wid, usages, config, metrics)
        findings.extend(local_findings)
        workflow = workflow_timing(workload, spans, usages)
        for item in workflow["contributors"]:
            ref = f"{wid}/workflow/{item['kind']}/observed_ns"
            metrics[ref] = Metric(
                value=item["observed_ns"],
                unit="ns",
                status="derived",
                method="most_specific_observed_interval_partition",
                source_refs=item["source_refs"],
            )
            item["metric_ref"] = ref
        for finding in local_findings:
            workflow["candidates"].append(
                {
                    "category": finding.rule_id,
                    "status": "pattern_not_cause",
                    "finding_ref": finding.finding_id,
                    "metric_refs": finding.metric_refs,
                    "source_refs": finding.evidence_refs,
                    "next_check": finding.next_check,
                    "benefit": finding.benefit,
                }
            )
        workload_results.append(
            {
                "workload": workload.model_dump(mode="json"),
                "workflow_timing": workflow,
                "service_windows": service_windows(service_metrics),
                "coverage": coverage,
                "stages": stages,
                "rounds": rounds,
                "spans": span_details,
                "usages": [u.model_dump(mode="json") for u in usages],
                "tokens": {k: v.model_dump(mode="json") for k, v in summary.items()},
                "caches": cache_summary(caches),
                "cost": cost_summary(usages, costs, prices),
                "graph": graph,
                "observability": capabilities(evidence, workload, spans, graph),
                "request_timings": request_timings(spans, usages),
                "service_lifecycle": {
                    "span_ids": [s.span_id for s in spans if s.operation == "service_startup"],
                    "timing": timing_summary(
                        [s for s in spans if s.operation == "service_startup"], evidence
                    ),
                    "meaning": "Cold start is separate from client inference call latency; task "
                    "wall time may include both. No phase split is inferred.",
                },
                "layer_bounds": layer_bounds,
                "priority": sorted(
                    [b for b in layer_bounds if b["benefit"].get("saved_ns") is not None],
                    key=lambda b: -b["benefit"]["saved_ns"],
                ),
                "unknown_layers": [
                    b["layer"] for b in layer_bounds if b["benefit"].get("saved_ns") is None
                ],
                "activities": [
                    a.model_dump(mode="json") for a in evidence.activities if a.workload_id == wid
                ],
                "dependencies": [
                    d.model_dump(mode="json") for d in evidence.dependencies if d.workload_id == wid
                ],
            }
        )
    return AnalysisResult(
        workloads=workload_results,
        metrics=metrics,
        findings=findings,
        evidence_index=index,
        config_digest=hashlib.sha256(
            json.dumps(
                {"analysis": config.model_dump(mode="json"), "prices": prices},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest(),
        warnings=warnings,
        service_metrics=service_metrics,
    )
