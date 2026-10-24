"""Native evidence, raw logs and explicit OTLP/HTTP JSON imports."""

import json
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

from agent_infertriage.models import (
    RECORD_TYPES,
    Clock,
    EvidenceSet,
    Manifest,
    Record,
    Source,
    Span,
    Usage,
    Workload,
)

MAX_BYTES = 100 * 1024 * 1024
MAX_RECORDS = 100_000


def read_limited(path: Path, limit: int = MAX_BYTES) -> str:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"Missing file or input exceeds {limit} bytes: {path.name}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input exceeds byte limit")
    return data.decode("utf-8")


def select(evidence: EvidenceSet, workload_ids: set[str]) -> EvidenceSet:
    result = evidence.model_copy(deep=True)
    result.manifest.workloads = [
        w for w in result.manifest.workloads if w.workload_id in workload_ids
    ]
    for name in ("spans", "activities", "dependencies", "usages", "caches", "costs", "contents"):
        setattr(result, name, [r for r in getattr(result, name) if r.workload_id in workload_ids])
    return result


def validate(evidence: EvidenceSet) -> list[str]:
    warnings = list(evidence.warnings)
    workloads = {w.workload_id for w in evidence.manifest.workloads}
    if len(workloads) != len(evidence.manifest.workloads) or not workloads:
        raise ValueError("Workload IDs must be nonempty and unique")
    all_records: list[Record] = []
    for name in ("spans", "activities", "dependencies", "usages", "caches", "costs", "contents"):
        all_records.extend(getattr(evidence, name))
    if len(all_records) > MAX_RECORDS:
        raise ValueError("Input exceeds 100,000 normalized records")
    ids: set[str] = set()
    for record in all_records:
        if record.record_id in ids:
            raise ValueError(f"Duplicate record_id: {record.record_id}")
        ids.add(record.record_id)
        if record.workload_id not in workloads:
            raise ValueError(f"Unknown workload reference: {record.workload_id}")
    span_ids = {(s.workload_id, s.span_id) for s in evidence.spans}
    if len(span_ids) != len(evidence.spans):
        raise ValueError("Conflicting duplicate span IDs")
    activity_ids = {(a.workload_id, a.activity_id) for a in evidence.activities}
    if len(activity_ids) != len(evidence.activities):
        raise ValueError("Conflicting duplicate activity IDs")
    clock_ids = {c.clock_id for c in evidence.manifest.clock_domains}
    if len(clock_ids) != len(evidence.manifest.clock_domains):
        raise ValueError("Duplicate clock ID")
    for workload in evidence.manifest.workloads:
        if workload.clock_domain not in clock_ids:
            raise ValueError(f"Unknown workload clock: {workload.clock_domain}")
    for span in evidence.spans:
        if span.clock_domain not in clock_ids:
            raise ValueError(f"Unknown clock: {span.clock_domain}")
        if span.parent_id and (span.workload_id, span.parent_id) not in span_ids:
            warnings.append(f"Missing parent for span {span.span_id}; timing coverage is partial")
    parents = {(s.workload_id, s.span_id): s.parent_id for s in evidence.spans}
    done: set[tuple[str, str]] = set()
    for key in parents:
        chain: set[tuple[str, str]] = set()
        current = key
        while current in parents and current not in done:
            if current in chain:
                raise ValueError("Cycle in span containment")
            chain.add(current)
            parent = parents[current]
            if parent is None:
                break
            current = (key[0], parent)
        done.update(chain)
    for usage in evidence.usages:
        if usage.span_id and (usage.workload_id, usage.span_id) not in span_ids:
            warnings.append(f"Usage {usage.record_id} references an unavailable span")
    return list(dict.fromkeys(warnings))


def load_native(path: Path) -> EvidenceSet:
    manifest_path = path / "manifest.json"
    manifest_text = read_limited(manifest_path)
    manifest = Manifest.model_validate_json(manifest_text)
    root = path.resolve()
    events_path = (root / manifest.events_file).resolve()
    if not events_path.is_relative_to(root):
        raise ValueError("events_file must stay inside the evidence directory")
    events_text = read_limited(events_path, MAX_BYTES - len(manifest_text.encode()))
    result = EvidenceSet(manifest=manifest)
    result.sources["events"] = str(events_path)
    total_bytes = len(events_text.encode()) + len(manifest_text.encode())
    field_map = {
        "span": "spans",
        "activity": "activities",
        "dependency": "dependencies",
        "usage": "usages",
        "cache": "caches",
        "cost": "costs",
        "content": "contents",
    }
    count = 0
    for line_no, line in enumerate(events_text.splitlines(), 1):
        if not line.strip():
            continue
        count += 1
        if count > MAX_RECORDS:
            raise ValueError("Input exceeds record limit")
        raw = json.loads(line)
        kind = raw.get("record_type")
        if kind not in RECORD_TYPES:
            raise ValueError(f"Unsupported record_type at events line {line_no}")
        raw.setdefault("source_refs", [f"events:line:{line_no}"])
        record = RECORD_TYPES[kind].model_validate(raw)
        getattr(result, field_map[kind]).append(record)
    source_ids: set[str] = {"events"}
    for source in manifest.sources:
        if source.source_id in source_ids:
            raise ValueError("Duplicate/reserved source ID")
        source_ids.add(source.source_id)
        source_path = (root / source.path).resolve()
        if not source_path.is_relative_to(root):
            raise ValueError("Evidence source paths must stay inside the evidence directory")
        optional_metrics = source.format in ("prometheus-text", "prometheus-range-json")
        if source_path.is_file() and source_path.stat().st_size > MAX_BYTES - total_bytes:
            raise ValueError("Total evidence input exceeds byte limit")
        try:
            source_text = read_limited(source_path, MAX_BYTES - total_bytes)
        except (ValueError, OSError) as exc:
            if not optional_metrics:
                raise
            result.warnings.append(f"Optional metrics source {source.source_id} unavailable: {exc}")
            continue
        total_bytes += len(source_text.encode())
        result.sources[source.source_id] = str(source_path)
        if optional_metrics:
            from agent_infertriage.prometheus import parse_source

            try:
                parsed = parse_source(source_text, source)
                if sum(len(s["samples"]) for s in result.service_series + parsed) > MAX_RECORDS:
                    raise ValueError("Total metrics evidence exceeds sample limit")
                result.service_series.extend(parsed)
            except (ValueError, TypeError, KeyError) as exc:
                result.warnings.append(f"Optional metrics source {source.source_id} invalid: {exc}")
    result.import_report = {"format": "native-v1", "records": count, "bytes": total_bytes}
    result.warnings = validate(result)
    return result


def load_raw(path: Path) -> EvidenceSet:
    read_limited(path)
    result = EvidenceSet(
        manifest=Manifest(
            workloads=[Workload(workload_id="raw-log", name=path.name)],
            sources=[Source(source_id="log", path=path.name)],
            content_policy="model_allowed",
        ),
        sources={"log": str(path.resolve())},
        warnings=["Raw text has no verified stage timing or execution dependencies."],
        import_report={"format": "raw-text", "records": 0},
    )
    return result


def _attributes(items: list[dict[str, Any]]) -> dict[str, Any]:
    def decode(value: dict[str, Any]) -> Any:
        for key in ("stringValue", "boolValue", "doubleValue", "intValue"):
            if key in value:
                return int(value[key]) if key == "intValue" else value[key]
        if "arrayValue" in value:
            return [decode(item) for item in value["arrayValue"].get("values", [])]
        if "kvlistValue" in value:
            return _attributes(value["kvlistValue"].get("values", []))
        return None

    return {item["key"]: decode(item.get("value", {})) for item in items}


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("OTLP timestamps/counts must be exact integers, not floats")
    return int(value)


def load_otlp(path: Path, mapping_path: Path | None = None) -> EvidenceSet:
    text = read_limited(path)
    config: dict[str, Any] = {}
    if mapping_path:
        config = tomllib.loads(read_limited(mapping_path))
    mapping = config.get("attributes", {})
    defaults = {
        "operation": "gen_ai.operation.name",
        "model": "gen_ai.request.model",
        "input_tokens": "gen_ai.usage.input_tokens",
        "output_tokens": "gen_ai.usage.output_tokens",
        "request_id": "gen_ai.response.id",
        "agent_id": "infertriage.agent_id",
        "round_id": "infertriage.workflow_round_id",
        "stages": "infertriage.stages",
        "observation_level": "infertriage.observation_level",
        "attempt_id": "infertriage.attempt_id",
        "provider": "gen_ai.provider.name",
    }
    keys = defaults | mapping
    try:
        documents = [json.loads(text)]
    except json.JSONDecodeError:
        documents = [json.loads(line) for line in text.splitlines() if line.strip()]
    spans: list[Span] = []
    usages: list[Usage] = []
    by_trace: dict[str, list[Span]] = defaultdict(list)
    ignored: set[str] = set()
    clocks: dict[str, Clock] = {}
    for doc_no, document in enumerate(documents):
        for res_no, resource in enumerate(document.get("resourceSpans", [])):
            clock_id = f"otlp-unix:{doc_no}:{res_no}"
            clocks[clock_id] = Clock(clock_id=clock_id)
            for scope_no, scope in enumerate(resource.get("scopeSpans", [])):
                for span_no, raw in enumerate(scope.get("spans", [])):
                    if len(spans) + len(usages) >= MAX_RECORDS:
                        raise ValueError("OTLP exceeds normalized record limit")
                    trace = str(raw["traceId"]).lower()
                    sid = str(raw["spanId"]).lower()
                    if len(trace) != 32 or len(sid) != 16:
                        raise ValueError("OTLP requires 32/16-character hexadecimal trace/span IDs")
                    int(trace, 16)
                    int(sid, 16)
                    attrs = _attributes(raw.get("attributes", []))
                    ignored.update(set(attrs) - set(keys.values()))
                    ref = (
                        f"otlp:json:{doc_no}/resourceSpans/{res_no}/scopeSpans/"
                        f"{scope_no}/spans/{span_no}"
                    )
                    parent = raw.get("parentSpanId") or None
                    span = Span(
                        record_id=f"span:{trace}:{sid}",
                        workload_id=trace,
                        span_id=sid,
                        parent_id=str(parent).lower() if parent else None,
                        name=raw.get("name", ""),
                        operation=str(attrs.get(keys["operation"], "custom")),
                        start_ns=_integer(raw["startTimeUnixNano"]),
                        end_ns=_integer(raw["endTimeUnixNano"]),
                        clock_domain=clock_id,
                        observation_level=attrs.get(keys["observation_level"]),
                        agent_id=attrs.get(keys["agent_id"]),
                        round_id=attrs.get(keys["round_id"]),
                        stages=attrs.get(keys["stages"], []),
                        source_refs=[ref],
                        request_id=attrs.get(keys["request_id"]),
                        attempt_id=attrs.get(keys["attempt_id"]),
                        attributes=attrs
                        | {
                            "otlp.resource": resource.get("resource", {}),
                            "otlp.scope": scope.get("scope", {}),
                            "otlp.links": raw.get("links", []),
                            "otlp.events": raw.get("events", []),
                            "otlp.status": raw.get("status", {}),
                        },
                    )
                    spans.append(span)
                    by_trace[trace].append(span)
                    input_value = attrs.get(keys["input_tokens"])
                    output_value = attrs.get(keys["output_tokens"])
                    if input_value is not None or output_value is not None:
                        usages.append(
                            Usage(
                                record_id=f"usage:{trace}:{sid}",
                                workload_id=trace,
                                span_id=sid,
                                request_id=span.request_id or f"span:{sid}",
                                attempt_id=span.attempt_id or "0",
                                agent_id=span.agent_id,
                                round_id=span.round_id,
                                input_tokens=_integer(input_value)
                                if input_value is not None
                                else None,
                                output_tokens=_integer(output_value)
                                if output_value is not None
                                else None,
                                model=str(attrs.get(keys["model"], "unknown")),
                                provider=str(attrs.get(keys["provider"], "unknown")),
                                raw_usage={k: v for k, v in attrs.items() if "usage" in k},
                                source_refs=[ref],
                            )
                        )
    if not spans:
        raise ValueError("No OTLP spans found (expected resourceSpans/scopeSpans/spans)")
    workloads = [
        Workload(
            workload_id=tid,
            start_ns=min(s.start_ns for s in group if s.start_ns is not None)
            if len({s.clock_domain for s in group}) == 1
            else None,
            end_ns=max(s.end_ns for s in group if s.end_ns is not None)
            if len({s.clock_domain for s in group}) == 1
            else None,
            clock_domain=group[0].clock_domain,
            graph_complete=False,
        )
        for tid, group in sorted(by_trace.items())
    ]
    result = EvidenceSet(
        manifest=Manifest(
            workloads=workloads,
            clock_domains=list(clocks.values()),
            sources=[Source(source_id="otlp", path=path.name, format="otlp-json")],
        ),
        spans=spans,
        usages=usages,
        sources={"otlp": str(path.resolve())},
        warnings=[
            "OTLP containment/links do not establish an execution DAG; graph remains unknown.",
            "OTLP cross-service clock synchronization is not verified.",
        ],
        import_report={
            "format": "otlp-json",
            "records": len(spans) + len(usages),
            "unmapped_attributes": sorted(ignored),
            "mapping": keys,
            "exporter_version": config.get("exporter_version", "unspecified"),
        },
    )
    result.warnings = validate(result)
    return result


def save_native(evidence: EvidenceSet, path: Path) -> None:
    validate(evidence)
    if not (path / evidence.manifest.events_file).resolve().is_relative_to(path.resolve()):
        raise ValueError("events_file must stay inside the evidence directory")
    path.mkdir(parents=True, exist_ok=False)
    manifest = evidence.manifest.model_copy(deep=True)
    # Preserve originals by explicit attachment, never by a parent-directory path escape.
    for source in manifest.sources:
        source_path = Path(evidence.sources[source.source_id])
        destination = path / f"source-{source.source_id}{source_path.suffix}"
        destination.write_text(read_limited(source_path), encoding="utf-8")
        source.path = destination.name
    (path / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    with (path / manifest.events_file).open("w", encoding="utf-8") as handle:
        for name in (
            "spans",
            "activities",
            "dependencies",
            "usages",
            "caches",
            "costs",
            "contents",
        ):
            for record in getattr(evidence, name):
                handle.write(record.model_dump_json() + "\n")


def load(path: Path, format_name: str = "native") -> EvidenceSet:
    if format_name == "raw-text":
        return load_raw(path)
    if format_name == "otlp-json":
        return load_otlp(path)
    if format_name == "native":
        return load_native(path)
    raise ValueError("Supported formats: native, otlp-json, raw-text")
