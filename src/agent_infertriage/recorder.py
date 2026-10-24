"""Opt-in L0 instrumentation, independent of model SDK and agent framework."""

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from agent_infertriage.evidence import save_native
from agent_infertriage.models import EvidenceSet, Manifest, Span, Usage, Workload


class Call:
    def __init__(self, recorder: "Recorder", span: Span) -> None:
        self.recorder, self.span = recorder, span

    def first_token(self) -> None:
        """Call at the first content token, not a headers/role/empty stream event."""
        if self.span.end_ns is not None:
            raise ValueError("First-token event must occur inside its active call")
        if "client_ttft_ns" not in self.span.attributes:
            self.span.attributes["client_ttft_ns"] = self.recorder.now() - (self.span.start_ns or 0)

    def usage(self, *, input_tokens: int | None, output_tokens: int | None, **details: Any) -> None:
        """Inclusive counts; cache and reasoning counts are subsets, never additional tokens."""
        if self.span.end_ns is not None:
            raise ValueError("Usage must be attached inside its active call")
        record = Usage(
            record_id=str(uuid.uuid4()),
            workload_id=self.recorder.workload_id,
            request_id=self.span.request_id or self.span.span_id,
            attempt_id=self.span.attempt_id or "0",
            span_id=self.span.span_id,
            agent_id=self.span.agent_id,
            round_id=self.span.round_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            source_refs=self.span.source_refs,
            **details,
        )
        with self.recorder.lock:
            self.recorder.evidence.usages.append(record)


class Recorder:
    """One process/task. Concurrent child calls are supported, never added as serial dependencies.

    Use a new output path. Export happens on context exit, including exceptions. This records
    only code explicitly wrapped by the caller; it does not inspect traffic or start services.
    """

    def __init__(
        self,
        output_dir: Path | str,
        name: str = "Agent workload",
        *,
        start_boundary: str = "recorder_enter",
        origin_ns: int | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.workload_id = str(uuid.uuid4())
        self.origin_ns = time.monotonic_ns()
        self.external_origin_ns = origin_ns
        self.lock = threading.Lock()
        self.parent: ContextVar[str | None] = ContextVar(f"parent-{self.workload_id}", default=None)
        self.entered = False
        self.closed = False
        self.finished = False
        self.active_spans = 0
        self.evidence = EvidenceSet(
            manifest=Manifest(
                workloads=[
                    Workload(
                        workload_id=self.workload_id,
                        name=name,
                        start_ns=0,
                        start_boundary=start_boundary,
                        source_refs=["recorder:monotonic"],
                    )
                ]
            )
        )

    def now(self) -> int:
        return time.monotonic_ns() - self.origin_ns

    def __enter__(self) -> "Recorder":
        if self.entered or self.output_dir.exists():
            raise ValueError("Recorder is single-use and output must not exist")
        self.entered = True
        current = time.monotonic_ns()
        if self.external_origin_ns is not None:
            if self.external_origin_ns < 0 or self.external_origin_ns > current:
                raise ValueError("Origin must be a past monotonic timestamp on the same host/clock")
            self.origin_ns = self.external_origin_ns
            sid = str(uuid.uuid4())
            self.evidence.spans.append(
                Span(
                    record_id=sid,
                    workload_id=self.workload_id,
                    span_id=sid,
                    name="Process launch through Agent initialization",
                    operation="agent_step",
                    observation_level="L0",
                    start_ns=0,
                    end_ns=self.now(),
                    attributes={
                        "step_kind": "agent_init",
                        "origin_provenance": "caller_same_host_monotonic",
                    },
                    source_refs=[f"recorder:{sid}"],
                )
            )
        else:
            self.origin_ns = current
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        workload = self.evidence.manifest.workloads[0]
        if not self.finished:
            workload.end_ns = self.now()
            workload.end_boundary = "recorder_exit"
            workload.completion_status = (
                "cancelled"
                if exc_type and issubclass(exc_type, KeyboardInterrupt)
                else "failed"
                if exc_type
                else "completed"
            )
        self.closed = True
        save_native(self.evidence, self.output_dir)

    def finish(self, boundary: str, *, end_span_id: str | None = None) -> None:
        """Seal the business stopwatch before reports/cleanup; never backdate past recorded work."""
        with self.lock:
            if not self.entered or self.closed or self.finished or self.active_spans:
                raise ValueError("Finish once inside Recorder, with all spans closed")
            end = self.now()
            if end_span_id is not None:
                matches = [s for s in self.evidence.spans if s.span_id == end_span_id]
                if len(matches) != 1 or matches[0].end_ns is None:
                    raise ValueError("Finish requires an existing completed span")
                end = matches[0].end_ns
            if any(s.end_ns is not None and s.end_ns > end for s in self.evidence.spans):
                raise ValueError("Finish would exclude recorded work")
            workload = self.evidence.manifest.workloads[0]
            workload.end_ns, workload.end_boundary = end, boundary
            workload.completion_status = "completed"
            self.finished = True

    def step(self, kind: str, name: str | None = None, **kwargs: Any) -> Any:
        """Explicit workflow steps. Decision wall time is not pure model thinking time."""
        from agent_infertriage.workflow import STEP_KINDS

        if kind not in STEP_KINDS:
            raise ValueError("Unknown workflow step kind")
        attrs = dict(kwargs.pop("attributes", {}) or {})
        attrs["step_kind"] = kind
        return self.span(name or kind, operation="agent_step", attributes=attrs, **kwargs)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        operation: str = "custom",
        agent_id: str | None = None,
        round_id: str | None = None,
        request_id: str | None = None,
        attempt_id: str = "0",
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[Call]:
        if not self.entered or self.closed or self.finished:
            raise ValueError("Use spans inside an active Recorder context")
        sid = str(uuid.uuid4())
        span = Span(
            record_id=sid,
            workload_id=self.workload_id,
            span_id=sid,
            parent_id=self.parent.get(),
            name=name,
            operation=operation,
            observation_level="L0",
            start_ns=self.now(),
            agent_id=agent_id,
            round_id=round_id,
            request_id=request_id,
            attempt_id=attempt_id,
            attributes=dict(attributes or {}),
            source_refs=[f"recorder:{sid}"],
        )
        token = self.parent.set(sid)
        with self.lock:
            if self.finished:
                raise ValueError("Cannot record after finish")
            self.active_spans += 1
            self.evidence.spans.append(span)
        try:
            yield Call(self, span)
        except BaseException as exc:
            span.attributes["error_type"] = type(exc).__name__
            raise
        finally:
            span.end_ns = self.now()
            self.parent.reset(token)
            with self.lock:
                self.active_spans -= 1

    def model_call(self, name: str = "Model call", **kwargs: Any) -> Any:
        kwargs.setdefault("request_id", str(uuid.uuid4()))
        return self.span(name, operation="llm", **kwargs)

    def startup_phase(self, phase: str, **kwargs: Any) -> Any:
        allowed = {
            "process_start",
            "model_load",
            "weights_load",
            "cuda_init",
            "compile",
            "cuda_graph_capture",
            "warmup",
            "readiness",
        }
        if phase not in allowed:
            raise ValueError(f"Startup phase must be one of {sorted(allowed)}")
        return self.span(
            phase, operation="service_startup", attributes={"startup_phase": phase}, **kwargs
        )
