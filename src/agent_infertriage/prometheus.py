"""Consume Prometheus, never turn aggregates into invented request measurements."""

import json
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

from agent_infertriage.models import Source


def _finite(value: Any) -> str | None:
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() else None
    except InvalidOperation:
        return None


def parse_source(text: str, source: Source) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if source.format == "prometheus-text":
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                timestamp = source.captured_at_ns
                if sample.timestamp is not None:
                    timestamp = int(Decimal(str(sample.timestamp)) * 1_000_000_000)
                result.append(
                    {
                        "source_id": source.source_id,
                        "endpoint_id": source.endpoint_id,
                        "engine_version": source.version or "unverified",
                        "name": sample.name,
                        "labels": dict(sample.labels),
                        "metric_type": family.type,
                        "help": family.documentation,
                        "samples": [{"timestamp_ns": timestamp, "value": _finite(sample.value)}],
                        "scope": "service_aggregate",
                        "source_refs": [f"{source.source_id}:metric:{sample.name}"],
                    }
                )
    elif source.format == "prometheus-range-json":
        data = json.loads(text, parse_float=Decimal)
        if data.get("status") != "success" or data.get("data", {}).get("resultType") != "matrix":
            raise ValueError("Expected successful Prometheus query_range matrix JSON")
        for series in data["data"].get("result", []):
            labels = dict(series.get("metric", {}))
            name = labels.pop("__name__", "unnamed_promql_result")
            result.append(
                {
                    "source_id": source.source_id,
                    "endpoint_id": source.endpoint_id,
                    "engine_version": source.version or "unverified",
                    "name": name,
                    "labels": labels,
                    "metric_type": "untyped",
                    "help": "query_range result",
                    "samples": [
                        {"timestamp_ns": int(Decimal(str(t)) * 1_000_000_000), "value": _finite(v)}
                        for t, v in series.get("values", [])
                    ],
                    "scope": "service_aggregate",
                    "source_refs": [f"{source.source_id}:series:{len(result)}"],
                }
            )
    if len(result) > 100_000 or sum(len(s["samples"]) for s in result) > 100_000:
        raise ValueError("Prometheus evidence exceeds 100,000 samples/series")
    return result


def summarize(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in series:
        identity = item["endpoint_id"] or ("source:" + item["source_id"])
        key = json.dumps([identity, item["name"], item["labels"]], sort_keys=True)
        groups[key].append(item)
    results = []
    for grouped in groups.values():
        first = grouped[0]
        samples = [s for item in grouped for s in item["samples"]]
        timestamped = sorted(
            [s for s in samples if s["timestamp_ns"] is not None], key=lambda s: s["timestamp_ns"]
        )
        resets = 0
        rates = []
        metadata_consistent = len({(s["metric_type"], s["engine_version"]) for s in grouped}) == 1
        is_counter = (
            metadata_consistent
            and first["metric_type"] == "counter"
            and not first["name"].endswith("_created")
        )
        if is_counter:
            for a, b in zip(timestamped, timestamped[1:], strict=False):
                if a["value"] is None or b["value"] is None:
                    continue
                delta = Decimal(b["value"]) - Decimal(a["value"])
                elapsed = b["timestamp_ns"] - a["timestamp_ns"]
                if delta < 0:
                    resets += 1
                elif elapsed > 0:
                    rates.append(
                        {
                            "start_ns": a["timestamp_ns"],
                            "end_ns": b["timestamp_ns"],
                            "rate_per_second": str(delta * 1_000_000_000 / elapsed),
                        }
                    )
        values = [Decimal(s["value"]) for s in samples if s["value"] is not None]
        results.append(
            first
            | {
                "samples": timestamped + [s for s in samples if s["timestamp_ns"] is None],
                "source_refs": sorted({r for item in grouped for r in item["source_refs"]}),
                "source_ids": sorted({item["source_id"] for item in grouped}),
                "metadata_consistent": metadata_consistent,
                "declared_versions": sorted({item["engine_version"] for item in grouped}),
                "counter_resets": resets,
                "counter_interval_rates": rates,
                "sample_count": len(samples),
                "minimum": str(min(values)) if values else None,
                "maximum": str(max(values)) if values else None,
                "time_window_ns": [timestamped[0]["timestamp_ns"], timestamped[-1]["timestamp_ns"]]
                if timestamped
                else None,
                "interpretation": (
                    "Service/window aggregate only; not a target request's latency or usage."
                ),
                "limitations": [
                    "No interval rates across conflicting declared versions/metric types",
                    "Histograms/summaries cannot reconstruct individual request samples",
                    "No request IDs are synthesized from labels, exemplars or time proximity",
                    "Engine version and metric units/denominators require source verification",
                ],
            }
        )
    return results


def capture(
    url: str,
    output: Path,
    query: str | None = None,
    start: str | None = None,
    end: str | None = None,
    step: str = "15s",
    timeout: float = 15,
) -> dict[str, Any]:
    """One explicit GET; no exporter, daemon, inference load or hidden retries."""
    if output.exists():
        raise FileExistsError("Metrics output already exists")
    if not url.startswith(("http://", "https://")):
        raise ValueError("Metrics endpoint must use HTTP(S)")
    if query is not None and (start is None or end is None):
        raise ValueError("query_range requires --start and --end")
    params = {"query": query, "start": start, "end": end, "step": step} if query else None
    target = url.rstrip("/") + "/api/v1/query_range" if query else url
    began = time.monotonic_ns()
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        with client.stream("GET", target, params=params) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise ValueError("Metrics response exceeds byte limit")
                chunks.append(chunk)
    payload = b"".join(chunks)
    format_name = "prometheus-range-json" if query else "prometheus-text"
    source = Source(
        source_id="capture", path=output.name, format=format_name, captured_at_ns=time.time_ns()
    )
    parsed = parse_source(payload.decode("utf-8"), source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(payload)
    return {
        "status": "captured",
        "format": format_name,
        "series_count": len(parsed),
        "captured_at_ns": source.captured_at_ns,
        "bytes": len(payload),
        "wall_time_ms": (time.monotonic_ns() - began) / 1e6,
        "new_inference_tokens": 0,
        "scope": "explicit read-only metrics acquisition; no workload inference requests",
    }
