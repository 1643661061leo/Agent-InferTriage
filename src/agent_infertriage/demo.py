"""Synthetic, hand-checkable three-round workload; never a claimed GPU measurement."""

from decimal import Decimal
from typing import Any

from agent_infertriage.models import (
    Activity,
    CacheSample,
    ContentBlock,
    CostItem,
    Dependency,
    EvidenceSet,
    Manifest,
    Span,
    Stage,
    Usage,
    Workload,
)

SECOND = 1_000_000_000


def make_demo() -> EvidenceSet:
    wid = "synthetic-three-rounds"
    evidence = EvidenceSet(
        manifest=Manifest(
            workloads=[
                Workload(
                    workload_id=wid,
                    name="Synthetic three-round agent (NOT measured)",
                    start_ns=0,
                    end_ns=30 * SECOND,
                    graph_complete=True,
                    source_refs=["synthetic:hand-specified"],
                )
            ]
        ),
        warnings=["All workload timings, tokens and DEMO currency charges are synthetic fixtures."],
    )
    evidence.spans.append(
        Span(
            record_id="root",
            workload_id=wid,
            span_id="root",
            name="Agent task",
            operation="agent",
            stages=["orchestration"],
            start_ns=0,
            end_ns=30 * SECOND,
            agent_id="builder",
            attributes={"children_complete": True},
            source_refs=["synthetic:root"],
        )
    )
    previous: str | None = None
    for round_no, input_count in enumerate((1000, 4000, 8000), 1):
        rid = f"round-{round_no}"
        request = f"request-{round_no}"
        base = (round_no - 1) * 10 * SECOND
        llm = f"llm-{round_no}"
        evidence.spans.append(
            Span(
                record_id=llm,
                workload_id=wid,
                span_id=llm,
                parent_id="root",
                operation="llm",
                name="Model request",
                stages=[],  # Request wrapper is not prompt-only execution time.
                start_ns=base + SECOND,
                end_ns=base + 9 * SECOND,
                request_id=request,
                agent_id="builder",
                round_id=rid,
                attributes={"children_complete": True},
                source_refs=[f"synthetic:{llm}"],
            )
        )
        segments: list[tuple[str, float, float, Stage]] = [
            ("dispatch", 0, 1, "orchestration"),
            ("prepare", 1, 2, "prompt"),
            ("cache", 2, 2.5, "cache"),
            ("queue", 2.5, 3, "scheduling"),
            ("kernel-a", 3, 6, "kernel"),
            ("kernel-b", 6, 9, "kernel"),
            ("read", 9, 10, "orchestration"),
        ]
        for name, start, end, stage in segments:
            sid = f"{name}-{round_no}"
            attrs: dict[str, Any] = {"children_complete": True}
            if name == "read":
                attrs |= {
                    "arguments": {"path": "README.md"},
                    "tool_name": "read_file",
                    "resource_version": "fixture-sha",
                    "read_only": True,
                    "idempotent": True,
                    "removal_safe": True,
                }
            evidence.spans.append(
                Span(
                    record_id=sid,
                    workload_id=wid,
                    span_id=sid,
                    parent_id="root" if name in ("dispatch", "read") else llm,
                    name=name,
                    operation="tool" if name == "read" else stage,
                    stages=[stage],
                    start_ns=base + int(start * SECOND),
                    end_ns=base + int(end * SECOND),
                    agent_id="builder",
                    round_id=rid,
                    stage_occurrence_id=f"{stage}-{rid}",
                    request_id=request if name not in ("dispatch", "read") else None,
                    kernel_invocation_id=sid if stage == "kernel" else None,
                    attributes=attrs,
                    source_refs=[f"synthetic:{sid}"],
                )
            )
            evidence.activities.append(
                Activity(
                    record_id=f"activity:{sid}",
                    workload_id=wid,
                    activity_id=sid,
                    duration_ns=int((end - start) * SECOND),
                    stages=[stage],
                    scope_span_id=sid,
                    source_refs=[f"synthetic:{sid}"],
                )
            )
            if previous:
                evidence.dependencies.append(
                    Dependency(
                        record_id=f"edge:{previous}:{sid}",
                        workload_id=wid,
                        from_id=previous,
                        to_id=sid,
                        source_refs=["synthetic:serial-dependencies"],
                    )
                )
            previous = sid
        evidence.usages.append(
            Usage(
                record_id=f"usage:{rid}",
                workload_id=wid,
                request_id=request,
                span_id=llm,
                agent_id="builder",
                round_id=rid,
                provider="synthetic",
                model="fixture-model",
                input_tokens=input_count,
                output_tokens=100,
                cache_read_tokens=0,
                cache_write_tokens=0,
                reasoning_tokens=20,
                source_refs=[f"synthetic:usage:{rid}"],
            )
        )
        evidence.caches.append(
            CacheSample(
                record_id=f"cache:{rid}",
                workload_id=wid,
                cache_type="prompt",
                scope="cache",
                unit="tokens",
                hits=0,
                queries=input_count,
                round_id=rid,
                interval_start_ns=base,
                agent_id="builder",
                interval_end_ns=base + 10 * SECOND,
                source_refs=[f"synthetic:cache:{rid}"],
            )
        )
        evidence.costs.append(
            CostItem(
                record_id=f"cost:{rid}",
                workload_id=wid,
                owner_event=f"request:{request}:0",
                amount=Decimal("0.01"),
                currency="DEMO",
                pricing_ref="synthetic-example-not-money",
                source_refs=[f"synthetic:cost:{rid}"],
            )
        )
        evidence.contents.append(
            ContentBlock(
                record_id=f"prompt:{rid}",
                workload_id=wid,
                request_id=request,
                role="system",
                text=f"Time: 2026-10-23T10:00:0{round_no}Z\n"
                + "You are an evidence-driven agent. Keep the stable prefix consistent. " * 5,
                source_refs=[f"synthetic:prompt:{rid}"],
            )
        )
        evidence.contents.append(
            ContentBlock(
                record_id=f"tool-content:{rid}",
                workload_id=wid,
                request_id=request,
                role="tool_result",
                text="Identical synthetic README content carried into the prompt.",
                content_key="synthetic-readme-v1",
                source_refs=[f"synthetic:tool-content:{rid}"],
            )
        )
    return evidence
