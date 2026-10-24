"""Product invariants: exact boundaries, nesting, parallelism, gaps and safe diagnostics."""

import time

import pytest

from agent_infertriage.analysis import analyze
from agent_infertriage.evidence import load_native
from agent_infertriage.models import EvidenceSet, Manifest, Source, Span, Workload
from agent_infertriage.prometheus import parse_source, summarize
from agent_infertriage.recorder import Recorder
from agent_infertriage.workflow import service_windows, workflow_timing


def span(sid, start, end, category, parent=None):
    return Span(
        record_id=sid,
        span_id=sid,
        workload_id="w",
        parent_id=parent,
        start_ns=start,
        end_ns=end,
        observation_level="L0",
        attributes={"step_kind": category},
        source_refs=[sid],
    )


def test_partition_nested_parallel_and_gaps_without_double_count():
    w = Workload(workload_id="w", start_ns=0, end_ns=100)
    spans = [
        span("parent", 10, 90, "tool_dispatch"),
        span("child", 20, 60, "document_read", "parent"),
        span("parallel", 40, 70, "agent_decision", "parent"),
    ]
    r = workflow_timing(w, spans, [])
    times = {c["kind"]: c["observed_ns"] for c in r["contributors"]}
    assert times == {
        "unattributed": 20,
        "tool_dispatch": 30,
        "document_read": 20,
        "agent_decision": 10,
        "parallel_overlap": 20,
    }
    assert r["reconciled"] and sum(times.values()) == 100
    assert r["observed_fraction"] == 0.8
    assert all(
        c["status"] == "unknown" for c in r["candidates"] if c["category"].startswith("handoff")
    )


def test_finish_uses_last_completed_call_not_export_or_cleanup(tmp_path):
    p = tmp_path / "evidence"
    with Recorder(p, start_boundary="agent_entry") as rec:
        with rec.step("agent_init"):
            pass
        with rec.model_call() as call:
            call.first_token()
            call.usage(input_tokens=12, output_tokens=3)
        rec.finish("last_inference_completed", end_span_id=call.span.span_id)
        with pytest.raises(ValueError):
            with rec.step("document_read"):
                pass
        with pytest.raises(ValueError):
            rec.finish("again")
    r = analyze(load_native(p)).workloads[0]["workflow_timing"]
    assert r["boundaries"]["start"] == "agent_entry"
    assert r["boundaries"]["end"] == "last_inference_completed"
    assert r["boundaries"]["end_ns"] == call.span.end_ns
    assert r["reconciled"]


def test_finish_rejects_active_unknown_or_earlier_span(tmp_path):
    with Recorder(tmp_path / "e") as rec:
        with rec.step("agent_init") as first:
            with pytest.raises(ValueError):
                rec.finish("early")
        with rec.step("document_read"):
            pass
        with pytest.raises(ValueError):
            rec.finish("early", end_span_id=first.span.span_id)
        with pytest.raises(ValueError):
            rec.finish("missing", end_span_id="missing")


def test_failure_preserves_boundary_and_unknown_thinking(tmp_path):
    p = tmp_path / "e"
    with pytest.raises(RuntimeError):
        with Recorder(p, start_boundary="agent_entry") as rec:
            with rec.model_call(attributes={"step_kind": "agent_decision"}):
                raise RuntimeError("example")
    r = analyze(load_native(p)).workloads[0]["workflow_timing"]
    assert r["boundaries"]["completion_status"] == "failed"
    assert r["boundaries"]["end"] == "recorder_exit"
    assert r["steps"][0]["pure_thinking_ns"] is None
    assert r["steps"][0]["input_tokens"] is None


def test_missing_clocks_and_boundaries_remain_unknown():
    w = Workload(workload_id="w")
    s = span("s", 0, 50, "inference")
    s.clock_domain = "remote"
    r = workflow_timing(w, [s], [])
    assert r["total_ns"] is None and r["excluded_span_count"] == 1
    assert not r["contributors"]


def test_service_window_delta_reset_histogram_and_scope():
    def series(values, histogram=False):
        raw = []
        for i, value in enumerate(values):
            text = (
                "# TYPE request_queue_time histogram\nrequest_queue_time_sum "
                if histogram
                else "# TYPE prefix_cache_hits_total counter\nprefix_cache_hits_total "
            )
            raw.extend(
                parse_source(
                    text + str(value) + "\n",
                    Source(
                        source_id=f"s{i}",
                        path="x",
                        format="prometheus-text",
                        captured_at_ns=i + 1,
                        endpoint_id="same",
                        version="pinned",
                    ),
                )
            )
        return service_windows(summarize(raw))[0]

    assert series([0, 12])["delta"] == "12"
    assert series([12, 1])["delta"] is None
    assert series([12, 1])["counter_reset"]
    assert series([0, 0.5], True)["delta"] == "0.5"
    assert series([12])["status"] == "unknown"
    assert series([0, 12])["scope"] == "service_window_not_request"
    assert series([0, 12])["counter_intervals"][0]["delta"] == "12"


def test_legacy_evidence_still_loads_without_boundary_claims():
    e = EvidenceSet(manifest=Manifest(workloads=[Workload(workload_id="w", start_ns=0, end_ns=5)]))
    r = analyze(e).workloads[0]["workflow_timing"]
    assert r["boundaries"]["start"] == "unspecified"
    assert r["contributors"][0]["kind"] == "unattributed"
    assert r["contributors"][0]["benefit"]["status"] == "unknown"


def test_parent_monotonic_origin_includes_process_initialization(tmp_path):
    origin = time.monotonic_ns()
    with Recorder(tmp_path / "e", origin_ns=origin, start_boundary="process_spawn") as rec:
        first = rec.evidence.spans[0]
        assert first.start_ns == 0 and first.attributes["step_kind"] == "agent_init"
        assert rec.origin_ns == origin and first.end_ns >= 0
    with pytest.raises(ValueError):
        with Recorder(tmp_path / "bad", origin_ns=time.monotonic_ns() + 10**12):
            pass


@pytest.mark.parametrize(
    "category", ["handoff_serialize", "handoff_transfer", "handoff_deserialize"]
)
def test_explicit_zero_duration_handoff_is_not_missing(category):
    w = Workload(workload_id="w", start_ns=0, end_ns=20)
    r = workflow_timing(w, [span("zero", 5, 5, category)], [])
    assert r["steps"][0]["inclusive_ns"] == 0
    assert not any(c["status"] == "unknown" for c in r["candidates"] if c["category"] == category)
    assert r["contributors"][0]["kind"] == "unattributed"


def test_readiness_envelope_is_not_added_to_child_waits():
    w = Workload(workload_id="w", start_ns=0, end_ns=100)
    r = workflow_timing(
        w,
        [
            span("ready", 0, 100, "readiness_wait"),
            span("probe", 0, 10, "health_check", "ready"),
            span("sleep", 10, 100, "poll_wait", "ready"),
        ],
        [],
    )
    assert sum(c["observed_ns"] for c in r["contributors"]) == 100
    assert r["phase_envelopes"][0]["inclusive_ns"] == 100
    assert r["candidates"][0]["additive"] is False


def test_cancelled_workflow_and_late_usage_are_not_silent(tmp_path):
    p = tmp_path / "cancel"
    with pytest.raises(KeyboardInterrupt):
        with Recorder(p) as rec:
            with rec.model_call() as call:
                pass
            with pytest.raises(ValueError):
                call.usage(input_tokens=1, output_tokens=1)
            raise KeyboardInterrupt()
    assert load_native(p).manifest.workloads[0].completion_status == "cancelled"


def test_service_metadata_conflict_does_not_produce_delta():
    raw = []
    for i, version in enumerate(["v1", "v2"]):
        raw.extend(
            parse_source(
                f"# TYPE prefix_cache_hits_total counter\nprefix_cache_hits_total {i}\n",
                Source(
                    source_id=f"s{i}",
                    path="x",
                    format="prometheus-text",
                    endpoint_id="same",
                    version=version,
                    captured_at_ns=i + 1,
                ),
            )
        )
    r = service_windows(summarize(raw))[0]
    assert r["delta"] is None and r["status"] == "unknown"


def test_analysis_tools_compact_provenance_without_losing_full_report(tmp_path):
    from agent_infertriage.agent import Tools
    from agent_infertriage.diagnosis import Session

    e = EvidenceSet(
        manifest=Manifest(workloads=[Workload(workload_id="w", start_ns=0, end_ns=100)])
    )
    e.spans = [span(str(i), i, i + 1, "health_check") for i in range(20)]
    a = analyze(e)
    tools = Tools(e, a, Session(tmp_path / "session", "agent"))
    view = tools.call("get_workflow_timing", {})
    c = next(x for x in view["contributors"] if x["kind"] == "health_check")
    assert c["source_ref_count"] == 20 and len(c["source_refs"]) == 2
    full = next(
        x for x in a.workloads[0]["workflow_timing"]["contributors"] if x["kind"] == "health_check"
    )
    assert len(full["source_refs"]) == 20
    assert tools.call("get_bottleneck_candidates", {})["candidates"]
    assert tools.call("get_service_windows", {})["scope"] == "service_not_request"
