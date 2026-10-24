from decimal import Decimal

import pytest

from agent_infertriage.accounting import cache_summary, cost_summary, deduplicate, token_summary
from agent_infertriage.analysis import analyze
from agent_infertriage.demo import make_demo
from agent_infertriage.models import CacheSample, CostItem, Usage


def usage(**kw):
    return Usage.model_validate(
        {
            "record_id": "u",
            "workload_id": "w",
            "request_id": "r",
            "input_tokens": 1000,
            "output_tokens": 100,
            **kw,
        }
    )


def test_inclusive_token_semantics():
    summary = token_summary([usage(cache_read_tokens=800, reasoning_tokens=60)])
    assert summary["total_tokens"].value == 1100
    assert summary["cache_read_tokens"].value == 800
    assert summary["reasoning_tokens"].value == 60


def test_three_copies_one_retry_and_conflict():
    a = usage()
    copies = [a, a.model_copy(update={"record_id": "b"}), a.model_copy(update={"record_id": "c"})]
    retry = a.model_copy(update={"record_id": "retry", "attempt_id": "1"})
    assert token_summary(deduplicate(copies + [retry]))["total_tokens"].value == 2200
    bad = a.model_copy(update={"record_id": "bad", "input_tokens": 900})
    result = token_summary(deduplicate([a, bad]))
    assert result["total_tokens"].value is None


def test_missing_usage_is_not_zero():
    e = make_demo()
    e.usages.pop()
    result = analyze(e).workloads[0]
    assert result["tokens"]["total_tokens"]["value"] is None
    assert result["tokens"]["total_tokens"]["known_subtotal"] == 5200
    assert result["tokens"]["total_tokens"]["unknown_count"] == 1


def cache(i, hits, queries, **kw):
    return CacheSample(
        record_id=str(i),
        workload_id="w",
        cache_type="prefix",
        scope="request",
        unit="tokens",
        hits=hits,
        queries=queries,
        **kw,
    )


def test_cache_uses_weighted_denominator():
    assert cache_summary([cache(0, 1, 1), cache(1, 0, 9)])[0]["hit_rate"] == 0.1


def test_cache_units_gauges_counters_and_overlap_do_not_mix():
    samples = [
        cache(0, 1, 1),
        cache(1, 0, 9, sample_kind="cumulative"),
        CacheSample(
            record_id="g",
            workload_id="w",
            cache_type="kv",
            scope="engine",
            unit="bytes",
            occupancy=100,
            capacity=200,
            sample_kind="gauge",
        ),
    ]
    groups = cache_summary(samples)
    assert len(groups) == 3
    assert all(g["hit_rate"] is None for g in groups if g["sample_kind"] != "delta")
    overlapping = [
        cache(0, 1, 1, interval_start_ns=0, interval_end_ns=10),
        cache(1, 0, 9, interval_start_ns=9, interval_end_ns=20),
    ]
    assert cache_summary(overlapping)[0]["hit_rate"] is None


def test_unpriced_and_billed_costs_currency_and_alternatives():
    assert cost_summary([usage()], [], [])["unpriced_items"]
    items = [
        CostItem(
            record_id=str(i),
            workload_id="w",
            owner_event=owner,
            amount=Decimal(amount),
            currency=currency,
            pricing_ref="fixture",
            basis=basis,
            method="billed",
        )
        for i, (owner, amount, currency, basis) in enumerate(
            [
                ("request:r:0", "0.01", "USD", "primary"),
                ("request:r:0", "0.01", "USD", "primary"),
                ("gpu", "2", "USD", "alternative"),
                ("host", "1", "CNY", "primary"),
                ("kernel", "0.003", "USD", "breakdown"),
            ]
        )
    ]
    result = cost_summary([usage()], items, [])
    assert result["known_subtotal_by_currency"] == {"CNY": "1", "USD": "0.01"}
    assert len(result["alternatives_and_breakdowns"]) == 2


def test_costs_visible_per_stage_and_round_without_double_charging():
    result = analyze(make_demo()).workloads[0]
    assert result["cost"]["known_subtotal_by_currency"] == {"DEMO": "0.03"}
    assert result["stages"]["kernel"]["cost"]["known_subtotal_by_currency"] == {"DEMO": "0.03"}
    rounds = [r for r in result["rounds"] if r["round_id"]]
    assert all(r["cost"]["known_subtotal_by_currency"] == {"DEMO": "0.01"} for r in rounds)
    assert result["tokens"]["total_tokens"]["value"] == 13300


@pytest.mark.parametrize(
    "kw", [{"input_tokens": -1}, {"cache_read_tokens": 1001}, {"reasoning_tokens": 101}]
)
def test_invalid_usage_rejected(kw):
    with pytest.raises(ValueError):
        usage(**kw)
