"""Request-owned usage, separately scoped caches and auditable Decimal prices."""

import json
import tomllib
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from agent_infertriage.models import CacheSample, CostItem, Metric, Usage


def deduplicate(usages: list[Usage]) -> list[Usage]:
    groups: dict[tuple[str, str, str], list[Usage]] = defaultdict(list)
    for usage in usages:
        groups[(usage.workload_id, usage.request_id, usage.attempt_id)].append(usage)
    result: list[Usage] = []
    for key in sorted(groups):
        group = groups[key]
        update: dict[str, Any] = {}
        conflict = False
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "span_id",
            "agent_id",
            "round_id",
        ):
            values = {getattr(item, field) for item in group if getattr(item, field) is not None}
            update[field] = next(iter(values)) if len(values) == 1 else None
            conflict |= len(values) > 1
        for field in ("provider", "model"):
            names = {getattr(u, field) for u in group if getattr(u, field) != "unknown"}
            update[field] = next(iter(names)) if len(names) == 1 else "unknown"
            conflict |= len(names) > 1
        update["source_refs"] = sorted({ref for item in group for ref in item.source_refs})
        update["raw_usage"] = {item.record_id: item.raw_usage for item in group}
        if conflict or any(u.normalized_semantics == "unknown" for u in group):
            update["usage_status"] = "unknown"
        elif any(u.usage_status == "estimated" for u in group):
            update["usage_status"] = "estimated"
        # Revalidate cross-field constraints after merging complementary observations.
        merged = group[0].model_dump() | update
        if conflict:
            merged["normalized_semantics"] = "unknown"
        result.append(Usage.model_validate(merged))
    return result


def token_summary(usages: list[Usage], scope: str = "") -> dict[str, Metric]:
    result: dict[str, Metric] = {}
    refs = sorted({r for u in usages for r in u.source_refs})
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "total_tokens",
    ):
        values: list[int] = []
        unknown = 0
        for usage in usages:
            value = getattr(usage, field, None)
            if field == "total_tokens" and (
                usage.input_tokens is not None and usage.output_tokens is not None
            ):
                value = usage.input_tokens + usage.output_tokens
            if value is None or usage.normalized_semantics == "unknown":
                unknown += 1
            else:
                values.append(value)
        complete = bool(usages) and not unknown
        result[field] = Metric(
            value=sum(values) if complete else None,
            unit="tokens",
            status=(
                "estimated" if any(u.usage_status == "estimated" for u in usages) else "derived"
            )
            if complete
            else "unknown",
            method="sum_unique_request_attempts_inclusive_input_output",
            scope=scope,
            source_refs=refs,
            known_subtotal=sum(values),
            unknown_count=unknown,
            reason="" if complete else "No usage observations or incomplete/ambiguous usage",
        )
    return result


def cache_summary(samples: list[CacheSample]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[CacheSample]] = defaultdict(list)
    for sample in samples:
        groups[(sample.cache_type, sample.scope, sample.unit, sample.sample_kind)].append(sample)
    result: list[dict[str, Any]] = []
    for (kind, scope, unit, sample_kind), group in sorted(groups.items()):
        hits = sum(s.hits for s in group if s.hits is not None)
        queries = sum(s.queries for s in group if s.queries is not None)
        complete = all(s.hits is not None and s.queries is not None for s in group)
        # Only explicit disjoint deltas can be added; counters/gauges are kept as samples.
        intervals = sorted(
            (s.interval_start_ns, s.interval_end_ns)
            for s in group
            if s.interval_start_ns is not None and s.interval_end_ns is not None
        )
        overlaps = any(b[0] < a[1] for a, b in zip(intervals, intervals[1:], strict=False))
        aggregatable = sample_kind == "delta" and not overlaps
        result.append(
            {
                "cache_type": kind,
                "scope": scope,
                "unit": unit,
                "sample_kind": sample_kind,
                "hits": hits if aggregatable and complete else None,
                "queries": queries if aggregatable and complete else None,
                "hit_rate": hits / queries if aggregatable and complete and queries else None,
                "reason": ""
                if aggregatable and complete and queries
                else "Missing denominator, zero queries, non-delta or overlapping intervals",
                "samples": [s.model_dump(mode="json") for s in group],
            }
        )
    return result


def load_prices(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    prices = data.get("prices", [])
    if not isinstance(prices, list):
        raise ValueError("prices.toml must contain [[prices]] entries")
    for price in prices:
        for key in ("provider", "model", "currency", "effective_at", "scheme"):
            if key not in price:
                raise ValueError(f"Price entry missing {key}")
        if price["scheme"] not in ("flat", "split_cache"):
            raise ValueError("Pricing scheme must be flat or split_cache")
        for key, value in price.items():
            if key.endswith("_per_million"):
                amount = Decimal(str(value))
                if not amount.is_finite() or amount < 0:
                    raise ValueError("Invalid price")
    keys = [(p["provider"], p["model"]) for p in prices]
    if len(set(keys)) != len(keys):
        raise ValueError("Provide exactly one effective price per provider/model")
    return prices


def price_usage(usage: Usage, prices: list[dict[str, Any]]) -> dict[str, Any]:
    match = next(
        (p for p in prices if p["provider"] == usage.provider and p["model"] == usage.model), None
    )
    base = {
        "owner_event": f"request:{usage.request_id}:{usage.attempt_id}",
        "status": "unknown",
        "amount": None,
        "currency": None,
        "source_refs": usage.source_refs,
        "method": "estimated",
    }
    if (
        not match
        or usage.input_tokens is None
        or usage.output_tokens is None
        or usage.normalized_semantics == "unknown"
    ):
        return base | {"reason": "No matching price or insufficient normalized usage"}
    try:
        parts = [
            (usage.input_tokens, "input_per_million"),
            (usage.output_tokens, "output_per_million"),
        ]
        if match["scheme"] == "split_cache":
            read, write = usage.cache_read_tokens, usage.cache_write_tokens
            if read is None or write is None or read + write > usage.input_tokens:
                return base | {"reason": "Cache pricing requires known disjoint read/write counts"}
            parts = [
                (usage.input_tokens - read - write, "input_per_million"),
                (read, "cache_read_per_million"),
                (write, "cache_write_per_million"),
                (usage.output_tokens, "output_per_million"),
            ]
        cost = sum(
            (Decimal(count) * Decimal(str(match[key])) / 1_000_000 for count, key in parts),
            Decimal(0),
        )
    except KeyError:
        return base | {"reason": "Missing rate for a required pricing bucket"}
    return base | {
        "status": "estimated",
        "amount": str(cost),
        "currency": match["currency"],
        "pricing_ref": json.dumps(match, sort_keys=True, default=str),
        "reason": "",
    }


def cost_summary(
    usages: list[Usage],
    items: list[CostItem],
    prices: list[dict[str, Any]],
) -> dict[str, Any]:
    owners: dict[str, list[CostItem]] = defaultdict(list)
    for item in items:
        if item.basis == "primary":
            owners[item.owner_event].append(item)
    chosen: dict[str, dict[str, Any]] = {}
    for owner, group in owners.items():
        billed = [i for i in group if i.method == "billed"]
        selected = billed or group
        values = {(i.amount, i.currency) for i in selected}
        if len(values) != 1:
            chosen[owner] = {
                "owner_event": owner,
                "status": "unknown",
                "amount": None,
                "currency": None,
                "reason": "Conflicting charges for same owner",
            }
        else:
            item = selected[0]
            chosen[owner] = item.model_dump(mode="json") | {"status": item.method}
    for usage in usages:
        key = f"request:{usage.request_id}:{usage.attempt_id}"
        chosen.setdefault(key, price_usage(usage, prices))
    amounts: dict[str, Decimal] = defaultdict(Decimal)
    unknown: list[dict[str, Any]] = []
    for charge in chosen.values():
        if charge.get("amount") is None:
            unknown.append(charge)
        else:
            amounts[charge["currency"]] += Decimal(str(charge["amount"]))
    return {
        "status": "partial" if unknown else ("known" if chosen else "unknown"),
        "known_subtotal_by_currency": {c: str(v) for c, v in sorted(amounts.items())},
        "unpriced_items": unknown,
        "items": list(chosen.values()),
        "alternatives_and_breakdowns": [
            i.model_dump(mode="json") for i in items if i.basis != "primary"
        ],
        "reason": "No cost/usage observations" if not chosen else "",
    }
