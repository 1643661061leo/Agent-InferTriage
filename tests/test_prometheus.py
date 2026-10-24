import json

import httpx
import pytest

from agent_infertriage.analysis import analyze
from agent_infertriage.demo import make_demo
from agent_infertriage.evidence import load_native, save_native
from agent_infertriage.models import Source
from agent_infertriage.prometheus import capture, parse_source, summarize


def source(name="a", **kw):
    return Source(source_id=name, path=f"{name}.prom", format="prometheus-text", **kw)


def test_counter_delta_reset_and_version_retained():
    series = []
    for i, value in enumerate((10, 20, 2, 7), 1):
        text = f'# TYPE tokens_total counter\ntokens_total{{model="test"}} {value}\n'
        series.extend(
            parse_source(
                text,
                source(
                    str(i),
                    captured_at_ns=i * 1_000_000_000,
                    endpoint_id="engine-a",
                    version="fixture",
                ),
            )
        )
    result = summarize(series)[0]
    assert result["counter_resets"] == 1
    assert [r["rate_per_second"] for r in result["counter_interval_rates"]] == ["10", "5"]
    assert result["engine_version"] == "fixture"


def test_unknown_endpoint_does_not_merge_different_sources():
    a = parse_source("queue 5\n", source("a"))
    b = parse_source("queue 10\n", source("b"))
    assert len(summarize(a + b)) == 2
    assert summarize(a)[0]["time_window_ns"] is None


def test_range_epoch_ns_precision_nonfinite_and_no_counter_guessing():
    text = (
        '{"status":"success","data":{"resultType":"matrix","result":['
        '{"metric":{"__name__":"vllm:request_queue_time_seconds_bucket","le":"5"},'
        '"values":[[1700000000.123456789,"10"],[1700000001,"NaN"]]}]}}'
    )
    s = Source(source_id="range", path="range.json", format="prometheus-range-json")
    result = summarize(parse_source(text, s))[0]
    assert result["samples"][0]["timestamp_ns"] == 1700000000123456789
    assert result["samples"][1]["value"] is None
    assert result["counter_interval_rates"] == []
    assert result["scope"] == "service_aggregate"


def test_prometheus_does_not_invent_request_latency_or_graph(tmp_path):
    e = make_demo()
    e.activities.clear()
    e.dependencies.clear()
    path = tmp_path / "metrics.prom"
    path.write_text("# TYPE vllm:num_requests_waiting gauge\nvllm:num_requests_waiting 999\n")
    e.manifest.sources.append(source("metrics", endpoint_id="shared"))
    e.sources["metrics"] = str(path)
    package = tmp_path / "evidence"
    save_native(e, package)
    result = analyze(load_native(package))
    assert result.service_metrics[0]["maximum"] == "999"
    assert result.workloads[0]["graph"]["status"] == "unknown"
    assert all(b["benefit"]["saved_ns"] is None for b in result.workloads[0]["layer_bounds"])


def test_capture_read_only_get_no_retry_or_overwrite(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="queue 1\n")

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    out = tmp_path / "captured.prom"
    receipt = capture("http://metrics.test/metrics", out)
    assert receipt["new_inference_tokens"] == 0
    assert len(calls) == 1 and calls[0].method == "GET"
    with pytest.raises(FileExistsError):
        capture("http://metrics.test/metrics", out)
    assert len(calls) == 1


def test_failed_capture_does_not_erase_trace_analysis(tmp_path, monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kw),
    )
    with pytest.raises(httpx.HTTPStatusError):
        capture("http://metrics.test/metrics", tmp_path / "failed.prom")
    assert not (tmp_path / "failed.prom").exists()
    assert analyze(make_demo()).workloads[0]["graph"]["status"] == "qualified"


def test_query_range_url_and_parameters(tmp_path, monkeypatch):
    calls = []
    body = {"status": "success", "data": {"resultType": "matrix", "result": []}}

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=json.dumps(body))

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    capture("http://prom.test", tmp_path / "range.json", "rate(tokens_total[1m])", "1", "20")
    assert calls[0].url.path == "/api/v1/query_range"
    assert calls[0].url.params["query"] == "rate(tokens_total[1m])"


@pytest.mark.parametrize("text", ['{"status":"error"}', '{"status":"success","data":{}}'])
def test_invalid_matrix_rejected(text):
    with pytest.raises(ValueError):
        parse_source(text, Source(source_id="s", path="s.json", format="prometheus-range-json"))
