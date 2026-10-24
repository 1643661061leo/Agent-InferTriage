"""Append-only diagnosis accounting, independent of historical workload usage."""

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_infertriage.accounting import cost_summary, token_summary
from agent_infertriage.models import Usage


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def usage_from_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Only the documented inclusive Chat Completions usage convention is accepted."""
    if not isinstance(raw, dict):
        raise ValueError("Model usage must be an object")

    def count(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Model usage must contain nonnegative integer counts")
        return int(value)

    details = raw.get("prompt_tokens_details") or {}
    output_details = raw.get("completion_tokens_details") or {}
    if not isinstance(details, dict) or not isinstance(output_details, dict):
        raise ValueError("Model usage details must be objects")
    normalized = {
        "input_tokens": count(raw.get("prompt_tokens")),
        "output_tokens": count(raw.get("completion_tokens")),
        "cache_read_tokens": count(details.get("cached_tokens")),
        "reasoning_tokens": count(output_details.get("reasoning_tokens")),
        "raw_usage": raw,
    }
    Usage.model_validate(
        {
            "record_id": "validation",
            "request_id": "validation",
            "workload_id": "validation",
            **normalized,
        }
    )
    return normalized


def build_usage(
    events: list[dict[str, Any]],
    diagnosis_id: str,
    prices: list[dict[str, Any]],
) -> dict[str, Any]:
    attempts: dict[str, dict[str, Any]] = {}
    tool_count = 0
    tool_ms = 0.0
    rounds = 0
    for event in events:
        if event["type"] == "model_attempt":
            key = event["attempt_id"]
            attempts[key] = attempts.get(key, {}) | event
        elif event["type"] == "tool_finished":
            tool_count += 1
            tool_ms += event["duration_ms"]
        elif event["type"] == "agent_round":
            rounds += 1
    usages = []
    for key, attempt in attempts.items():
        normalized = attempt.get("usage", {})
        usages.append(
            Usage(
                record_id=key,
                workload_id=diagnosis_id,
                request_id=key,
                provider=attempt.get("provider", "unknown"),
                model=attempt.get("model", "unknown"),
                input_tokens=normalized.get("input_tokens"),
                output_tokens=normalized.get("output_tokens"),
                cache_read_tokens=normalized.get("cache_read_tokens"),
                reasoning_tokens=normalized.get("reasoning_tokens"),
                raw_usage=normalized.get("raw_usage", {}),
                source_refs=[key],
                usage_status="reported" if normalized else "unknown",
            )
        )
    summary = token_summary(usages, diagnosis_id)
    tokens = {key: value.model_dump(mode="json") for key, value in summary.items()}
    if not usages:
        for key in tokens:
            tokens[key] = {
                "value": 0,
                "status": "derived",
                "unit": "tokens",
                "method": "no_new_model_calls",
                "known_subtotal": 0,
                "unknown_count": 0,
            }
    unknown = sum(u.input_tokens is None or u.output_tokens is None for u in usages)
    return {
        "analysis_round_count": rounds,
        "model_call_count": len(attempts),
        "tool_call_count": tool_count,
        "retry_count": sum(bool(a.get("is_retry")) for a in attempts.values()),
        "failed_call_count": sum(a.get("state") == "failed" for a in attempts.values()),
        "model_cumulative_ms": sum(a.get("duration_ms", 0) for a in attempts.values()),
        "tool_cumulative_ms": tool_ms,
        "tokens": tokens,
        "input_tokens": tokens["input_tokens"]["value"],
        "output_tokens": tokens["output_tokens"]["value"],
        "total_tokens": tokens["total_tokens"]["value"],
        "known_token_subtotal": tokens["total_tokens"].get("known_subtotal", 0),
        "usage_completeness": "partial" if unknown else "complete",
        "unknown_usage_call_count": unknown,
        "usage_source": "provider_reported_or_unknown",
        "cost": cost_summary(usages, [], prices)
        if usages
        else {
            "status": "known",
            "model_api_cost": "0",
            "local_compute_cost": "unpriced",
            "known_subtotal_by_currency": {},
            "unpriced_items": [],
        },
        "purposes": {
            "analysis_model_attempts": sum(
                a.get("purpose") == "analysis" for a in attempts.values()
            ),
            "probe_model_attempts": sum(a.get("purpose") == "probe" for a in attempts.values()),
        },
        "attempts": list(attempts.values()),
    }


class Session:
    def __init__(self, output_dir: Path, mode: str, max_seconds: float = 300) -> None:
        self.start_ns = time.monotonic_ns()
        self.started_at = utc_now()
        self.diagnosis_id = str(uuid.uuid4())
        self.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=False)
        self.mode = mode
        self.max_seconds = max_seconds
        self.events: list[dict[str, Any]] = []
        self.prices: list[dict[str, Any]] = []
        self.config_digest = ""
        self.event(
            "started",
            diagnosis_id=self.diagnosis_id,
            mode=mode,
            started_at=self.started_at,
            start_ns=self.start_ns,
        )

    def event(self, kind: str, **data: Any) -> dict[str, Any]:
        event = {
            "type": kind,
            "sequence": len(self.events),
            "monotonic_ns": time.monotonic_ns(),
            "timestamp": utc_now(),
        } | data
        with (self.output_dir / "diagnosis_events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.events.append(event)
        return event

    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - (time.monotonic_ns() - self.start_ns) / 1e9)

    def check_deadline(self) -> None:
        if self.remaining_seconds() <= 0:
            raise TimeoutError("Diagnosis time budget exhausted")

    def usage(self) -> dict[str, Any]:
        result = build_usage(self.events, self.diagnosis_id, self.prices)
        if self.mode == "agent-mock":
            result.update(
                {
                    "is_mock": True,
                    "actual_new_inference_tokens": 0,
                    "usage_source": "synthetic_mock_not_consumed",
                }
            )
        return result

    def finalize(self, status: str, workload_ids: list[str], error: str = "") -> dict[str, Any]:
        usage = self.usage()
        receipt = usage | {
            "diagnosis_id": self.diagnosis_id,
            "receipt_id": str(uuid.uuid4()),
            "mode": self.mode,
            "status": status,
            "workload_ids": workload_ids,
            "config_digest": self.config_digest,
            "started_at": self.started_at,
            "ended_at": utc_now(),
            "wall_time_ms": (time.monotonic_ns() - self.start_ns) / 1e6,
            "measurement_scope": "entry through report-body preparation and final usage snapshot; "
            "excludes final receipt serialization/file flush/terminal refresh",
            "model_time_scope": "client request-attempt durations, not server compute time",
            "error": error,
        }
        self.event("receipt", receipt=receipt)
        atomic_json(self.output_dir / "diagnosis_usage.json", receipt)
        return receipt


def recover(output_dir: Path) -> dict[str, Any]:
    start = time.monotonic_ns()
    events = []
    for line in (output_dir / "diagnosis_events.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            break  # A torn last append does not erase already durable events.
    if not events or events[0].get("type") != "started":
        raise ValueError("No valid diagnosis journal start event")
    for event in reversed(events):
        if event["type"] == "receipt":
            return dict(event["receipt"]) | {
                "recovery_wall_time_ms": (time.monotonic_ns() - start) / 1e6
            }
    initial = events[0]
    result = build_usage(events, initial["diagnosis_id"], []) | {
        "diagnosis_id": initial["diagnosis_id"],
        "mode": initial["mode"],
        "status": "incomplete",
        "started_at": initial["started_at"],
        "ended_at": None,
        "wall_time_ms": (events[-1]["monotonic_ns"] - initial["start_ns"]) / 1e6,
        "wall_time_is_lower_bound": True,
        "recovery_wall_time_ms": (time.monotonic_ns() - start) / 1e6,
        "measurement_scope": "last durable event; original diagnosis incomplete",
    }
    atomic_json(output_dir / "recovered_usage.json", result)
    return result
