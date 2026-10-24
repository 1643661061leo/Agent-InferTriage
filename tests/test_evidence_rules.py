import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from agent_infertriage.analysis import analyze
from agent_infertriage.cli import pipeline
from agent_infertriage.demo import make_demo
from agent_infertriage.evidence import load_native, load_otlp, save_native, validate
from agent_infertriage.models import Source


def test_native_round_trip_all_five_layers_and_repeated_kernel_invocations(tmp_path):
    path = tmp_path / "native"
    save_native(make_demo(), path)
    result = analyze(load_native(path))
    w = result.workloads[0]
    assert all(v["status"] == "observed" for v in w["coverage"].values())
    assert len([r for r in w["rounds"] if r["round_id"]]) == 3
    assert w["stages"]["kernel"]["span_count"] == 6
    assert {f.rule_id for f in result.findings} >= {
        "context_growth",
        "repeated_tool_call",
        "tool_result_amplification",
        "prefix_instability",
    }


@pytest.mark.parametrize("change", ["polling", "required_review", "version"])
def test_repeated_tool_false_positives_are_guarded(change):
    e = make_demo()
    for index, span in enumerate(s for s in e.spans if s.operation == "tool"):
        span.attributes["resource_version" if change == "version" else change] = (
            str(index) if change == "version" else True
        )
    assert not any(f.rule_id == "repeated_tool_call" for f in analyze(e).findings)


def test_context_growth_is_not_claimed_as_removable_or_causal():
    finding = next(f for f in analyze(make_demo()).findings if f.rule_id == "context_growth")
    assert finding.benefit["status"] == "unknown"
    assert finding.quality_risk
    assert finding.observation_levels == ["L0"]


def test_missing_order_does_not_infer_round_order_from_request_name():
    e = make_demo()
    for span in e.spans:
        if span.operation == "llm":
            span.start_ns = span.end_ns = None
    assert not any(f.rule_id == "context_growth" for f in analyze(e).findings)


@pytest.mark.parametrize("kind", ["events", "source"])
def test_manifest_paths_cannot_escape_evidence_dir(tmp_path, kind):
    package = tmp_path / "evidence"
    save_native(make_demo(), package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if kind == "events":
        manifest["events_file"] = "../outside.jsonl"
    else:
        manifest["sources"] = [{"source_id": "bad", "path": "../outside.log"}]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inside"):
        load_native(package)


def test_bad_optional_metrics_preserves_agent_evidence(tmp_path):
    package = tmp_path / "evidence"
    save_native(make_demo(), package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"] = [
        {"source_id": "absent", "path": "absent.prom", "format": "prometheus-text"}
    ]
    manifest_path.write_text(json.dumps(manifest))
    e = load_native(package)
    assert e.warnings
    assert analyze(e).workloads[0]["tokens"]["total_tokens"]["value"] == 13300


def test_duplicate_ids_and_containment_cycle_rejected():
    e = make_demo()
    e.spans.append(e.spans[0].model_copy())
    with pytest.raises(ValueError, match="Duplicate"):
        validate(e)
    e = make_demo()
    e.spans[0].parent_id = e.spans[1].span_id
    with pytest.raises(ValueError, match="Cycle"):
        validate(e)


def otlp_span(**updates):
    return {
        "traceId": "1" * 32,
        "spanId": "2" * 16,
        "name": "chat",
        "startTimeUnixNano": "1700000000123456789",
        "endTimeUnixNano": "1700000000123457790",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1000"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "100"}},
            {"key": "custom.debug", "value": {"stringValue": "preserved"}},
        ],
        **updates,
    }


def otlp_doc(spans):
    return {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}


def test_otlp_jsonl_exact_ns_usage_and_unknown_internal_layers(tmp_path):
    path = tmp_path / "spans.jsonl"
    path.write_text(json.dumps(otlp_doc([otlp_span()])) + "\n")
    e = load_otlp(path)
    assert e.spans[0].start_ns == 1700000000123456789
    assert "custom.debug" in e.import_report["unmapped_attributes"]
    w = analyze(e).workloads[0]
    assert w["request_timings"][0]["client_visible_latency"]["value"] == 1001
    assert w["tokens"]["total_tokens"]["value"] == 1100
    assert w["coverage"]["kernel"]["status"] == "unknown"
    assert w["graph"]["status"] == "unknown"


def test_otlp_float_ns_rejected_and_cross_resource_clocks_not_assumed(tmp_path):
    path = tmp_path / "spans.json"
    path.write_text(json.dumps(otlp_doc([otlp_span(startTimeUnixNano=1.7e18)])))
    with pytest.raises(ValueError, match="exact integers"):
        load_otlp(path)
    doc = otlp_doc([otlp_span()])
    doc["resourceSpans"].append({"scopeSpans": [{"spans": [otlp_span(spanId="3" * 16)]}]})
    path.write_text(json.dumps(doc))
    w = analyze(load_otlp(path)).workloads[0]
    assert w["workload"]["start_ns"] is None
    assert w["observability"]["L4"]["status"] == "unknown"


def test_sdk_generated_spans_with_explicit_otlp_projection(tmp_path):
    # Actual SDK execution, with an explicit formatter. This is NOT a production collector fixture.
    records = []

    def formatter(span):
        context = span.get_span_context()
        records.append(
            otlp_span(
                traceId=f"{context.trace_id:032x}",
                spanId=f"{context.span_id:016x}",
                startTimeUnixNano=str(span.start_time),
                endTimeUnixNano=str(span.end_time),
                name=span.name,
            )
        )
        return ""

    class Sink:
        def write(self, text):
            return len(text)

        def flush(self):
            pass

    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(ConsoleSpanExporter(out=Sink(), formatter=formatter))
    )
    with provider.get_tracer("infertriage.tests").start_as_current_span("instrumented-client"):
        sum(range(100))
    provider.shutdown()
    path = tmp_path / "sdk.json"
    path.write_text(json.dumps(otlp_doc(records)))
    assert len(load_otlp(path).spans) == 1


def test_html_escapes_hostile_names_and_has_no_executable_assets(tmp_path):
    package = tmp_path / "evidence"
    e = make_demo()
    payload = '</script><script>alert("bad")</script><img src="https://bad.test/x">'
    e.spans[0].name = payload
    save_native(e, package)
    out = tmp_path / "report"
    report, code = pipeline(package, out)
    assert code == 0
    html = (out / "report.html").read_text()
    assert payload not in html and "&lt;script&gt;" in html

    class AssetCheck(HTMLParser):
        def handle_starttag(self, tag, attrs):
            assert tag not in ("script", "iframe", "img", "link", "object", "form")

    AssetCheck().feed(html)
    assert report["diagnosis"]["receipt_id"] in html


def test_source_ids_cannot_be_used_as_output_path_traversal():
    with pytest.raises(ValueError):
        Source(source_id="../../outside", path="x")


def test_demo_matches_independent_frozen_answers():
    expected = json.loads((Path(__file__).parent / "fixtures/expected.json").read_text())[
        "three_round_demo"
    ]
    result = analyze(make_demo())
    w = result.workloads[0]
    assert w["tokens"]["total_tokens"]["value"] == expected["total_tokens"]
    assert w["cost"]["known_subtotal_by_currency"] == expected["cost_by_currency"]
    bounds = {b["layer"]: b["benefit"]["saved_ns"] / 1e9 for b in w["layer_bounds"]}
    assert bounds["scheduling"] == expected["queue_elimination_ceiling_seconds"]
    assert bounds["kernel"] == expected["kernel_elimination_ceiling_seconds"]
    repeated = next(f for f in result.findings if f.rule_id == "repeated_tool_call")
    assert (
        repeated.benefit["saved_ns"] / 1e9 == expected["repeated_tool_elimination_ceiling_seconds"]
    )
