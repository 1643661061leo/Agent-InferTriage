import pytest
from conftest import SECOND, graph_fixture
from hypothesis import given
from hypothesis import strategies as st

from agent_infertriage.analysis import analyze, interval_union, self_time
from agent_infertriage.graph import qualify, scenario, solve
from agent_infertriage.models import Activity, Clock, Span


def test_serial():
    e = graph_fixture([10, 15], [(0, 1)], 25)
    assert scenario(e, "w", ["a0"])["saved_ns"] == 10 * SECOND


def test_parallel_recompute_switches_critical_path():
    e = graph_fixture([10, 15, 0], [(0, 2), (1, 2)], 15)
    assert scenario(e, "w", ["a0"])["saved_ns"] == 0
    result = scenario(e, "w", ["a1"], "2")
    assert result["scenario"]["duration_ns"] == 10 * SECOND
    assert result["saved_ns"] == 5 * SECOND


def test_equal_branches():
    e = graph_fixture([10, 10], [], 10)
    assert qualify(e, e.manifest.workloads[0])["baseline"]["multiple_critical_paths"]
    assert scenario(e, "w", ["a0"])["saved_ns"] == 0
    assert scenario(e, "w", ["a0", "a1"])["saved_ns"] == 10 * SECOND


def test_release_constraints_and_zero():
    e = graph_fixture([10, 5], [(0, 1)], 25, {1: 20})
    assert scenario(e, "w", ["a0"])["saved_ns"] == 0
    zero = graph_fixture([0], [], 0)
    result = scenario(zero, "w", ["a0"])
    assert result["saved_ns"] == 0
    assert result["speedup"] is None
    assert result["reduction_fraction"] is None


@pytest.mark.parametrize(
    "mutation", ["incomplete", "cycle", "missing", "residual", "duplicate", "clock"]
)
def test_unqualified_graph_returns_unknown(mutation):
    e = graph_fixture([10, 15], [(0, 1)], 25)
    if mutation == "incomplete":
        e.manifest.workloads[0].graph_complete = False
    elif mutation == "cycle":
        e.dependencies.append(
            e.dependencies[0].model_copy(
                update={"record_id": "cycle", "from_id": "a1", "to_id": "a0"}
            )
        )
    elif mutation == "missing":
        e.dependencies[0].to_id = "absent"
    elif mutation == "residual":
        e.manifest.workloads[0].end_ns = 100 * SECOND
    elif mutation == "duplicate":
        e.activities[0].execution_key = e.activities[1].execution_key = "same-kernel"
    else:
        e.manifest.clock_domains.append(Clock(clock_id="other"))
        e.activities[0].clock_domain = "other"
    assert scenario(e, "w", ["a0"])["saved_ns"] is None


def test_overlap_self_time_and_out_of_bounds():
    parent = Span(
        record_id="p",
        workload_id="w",
        span_id="p",
        start_ns=0,
        end_ns=20,
        attributes={"children_complete": True},
    )
    children = [
        Span(record_id=str(i), workload_id="w", span_id=str(i), parent_id="p", start_ns=a, end_ns=b)
        for i, (a, b) in enumerate([(0, 12), (8, 20)])
    ]
    assert self_time(parent, children).value == 0
    children[1].end_ns = 21
    assert self_time(parent, children).status == "unknown"
    parent.attributes = {}
    assert self_time(parent, []).status == "unknown"


@given(st.lists(st.tuples(st.integers(0, 100), st.integers(0, 100)), max_size=30))
def test_union_matches_discrete_oracle(pairs):
    intervals = [(min(a, b), max(a, b)) for a, b in pairs]
    points = {i for a, b in intervals for i in range(a, b)}
    assert interval_union(intervals) == len(points)


@given(st.lists(st.integers(0, 1000), min_size=1, max_size=15), st.data())
def test_random_dag_weight_reduction_is_monotonic(durations, data):
    count = len(durations)
    edges = [(a, b) for a in range(count) for b in range(a + 1, count) if data.draw(st.booleans())]
    e = graph_fixture(durations, edges, 0)
    baseline = solve(e.activities, e.dependencies)["duration_ns"]
    weights = {a.activity_id: data.draw(st.integers(0, a.duration_ns)) for a in e.activities}
    after = solve(e.activities, e.dependencies, weights)["duration_ns"]
    assert 0 <= after <= baseline


def test_span_parents_are_not_dependencies():
    e = graph_fixture([], [], 10)
    e.spans = [
        Span(
            record_id="x",
            workload_id="w",
            span_id="x",
            stages=["prompt"],
            start_ns=0,
            end_ns=10 * SECOND,
        )
    ]
    result = analyze(e).workloads[0]
    assert result["graph"]["status"] == "unknown"
    assert all(item["benefit"]["saved_ns"] is None for item in result["layer_bounds"])


def test_unknown_activity_duplicate_solver_rejected():
    # Public solver must not silently collapse two physical activity IDs.
    a = Activity(record_id="1", workload_id="w", activity_id="same", duration_ns=1)
    with pytest.raises(ValueError):
        solve([a, a.model_copy(update={"record_id": "2"})], [])
