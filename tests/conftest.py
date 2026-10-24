from typing import Any

from agent_infertriage.models import Activity, Dependency, EvidenceSet, Manifest, Workload

SECOND = 1_000_000_000


def graph_fixture(
    durations: list[int],
    edges: list[tuple[int, int]],
    observed: int,
    releases: dict[int, int] | None = None,
) -> EvidenceSet:
    return EvidenceSet(
        manifest=Manifest(
            workloads=[
                Workload(
                    workload_id="w",
                    start_ns=0,
                    end_ns=observed * SECOND,
                    graph_complete=True,
                )
            ]
        ),
        activities=[
            Activity(
                record_id=f"a{i}",
                workload_id="w",
                activity_id=f"a{i}",
                duration_ns=d * SECOND,
                release_ns=(releases or {}).get(i, 0) * SECOND,
            )
            for i, d in enumerate(durations)
        ],
        dependencies=[
            Dependency(
                record_id=f"e{i}",
                workload_id="w",
                from_id=f"a{a}",
                to_id=f"a{b}",
            )
            for i, (a, b) in enumerate(edges)
        ],
    )


def response(action: dict[str, Any], usage: dict[str, Any] | None = None) -> dict[str, Any]:
    import json

    return {
        "choices": [{"message": {"content": json.dumps(action)}}],
        "usage": usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 20},
    }
