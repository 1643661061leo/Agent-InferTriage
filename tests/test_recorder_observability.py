import asyncio

import pytest

from agent_infertriage.analysis import analyze
from agent_infertriage.evidence import load_native
from agent_infertriage.models import Span
from agent_infertriage.recorder import Recorder


def test_black_box_latency_ttft_unknown_internals_and_startup(tmp_path):
    package = tmp_path / "recorded"
    with Recorder(package) as recorder:
        with recorder.startup_phase("model_load"):
            sum(range(100))
        with recorder.model_call(agent_id="a", round_id="1") as call:
            call.first_token()
            call.usage(input_tokens=100, output_tokens=5, cache_read_tokens=50)
    result = analyze(load_native(package)).workloads[0]
    assert result["observability"]["L0"]["status"] == "observed"
    assert all(
        result["observability"][level]["status"] == "unknown" for level in ("L1", "L2", "L3", "L4")
    )
    request = result["request_timings"][0]
    assert request["client_visible_latency"]["value"] > 0
    assert request["client_ttft"]["value"] >= 0
    assert all(v["value"] is None for v in request["server_phases"].values())
    assert request["kernel"]["status"] == "unknown"
    assert len(result["service_lifecycle"]["span_ids"]) == 1
    assert result["tokens"]["total_tokens"]["value"] == 105
    assert result["graph"]["status"] == "unknown"


def test_concurrent_async_calls_keep_rounds_without_serializing(tmp_path):
    path = tmp_path / "parallel"

    async def run():
        with Recorder(path) as rec:

            async def invoke(index):
                with rec.model_call(agent_id="a", round_id=str(index)) as call:
                    await asyncio.sleep(0)
                    call.usage(input_tokens=10, output_tokens=1)

            await asyncio.gather(*(invoke(i) for i in range(6)))

    asyncio.run(run())
    e = load_native(path)
    result = analyze(e).workloads[0]
    assert len(result["rounds"]) == 6
    assert len(result["request_timings"]) == 6
    assert len(e.dependencies) == 0
    assert result["tokens"]["total_tokens"]["value"] == 66


def test_capture_exports_on_exception_and_missing_usage_unknown(tmp_path):
    path = tmp_path / "failure"
    with pytest.raises(RuntimeError):
        with Recorder(path) as rec:
            with rec.model_call():
                raise RuntimeError("simulated transport failure")
    result = analyze(load_native(path)).workloads[0]
    assert result["tokens"]["total_tokens"]["value"] is None
    assert result["spans"][0]["attributes"]["error_type"] == "RuntimeError"


def test_request_phase_repetitions_and_observation_level(tmp_path):
    path = tmp_path / "phases"
    with Recorder(path) as rec:
        with rec.model_call(request_id="r") as call:
            call.usage(input_tokens=100, output_tokens=5)
    e = load_native(path)
    for i, (start, end) in enumerate([(10, 20), (18, 30)]):
        e.spans.append(
            Span(
                record_id=f"phase{i}",
                span_id=f"phase{i}",
                workload_id=rec.workload_id,
                observation_level="L2",
                operation="decode",
                request_id="r",
                start_ns=start,
                end_ns=end,
                engine_iteration_id=str(i),
            )
        )
    result = analyze(e).workloads[0]
    phase = result["request_timings"][0]["server_phases"]["decode"]
    assert phase["value"] == 20 and len(phase["occurrences"]) == 2
    assert result["observability"]["L2"]["status"] == "observed"
    assert result["observability"]["L4"]["status"] == "unknown"


def test_unknown_startup_phase_and_unentered_recorder_rejected(tmp_path):
    rec = Recorder(tmp_path / "invalid")
    with pytest.raises(ValueError):
        rec.startup_phase("guess_internal_compute")
    with pytest.raises(ValueError):
        with rec.model_call():
            pass
