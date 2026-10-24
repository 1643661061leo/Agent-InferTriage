"""Deterministic mock responses. Never evidence that a real model endpoint works."""

import json
from typing import Any

from agent_infertriage.agent import AgentConfig, Provider


def mock_provider() -> Provider:
    actions = iter(
        [
            {"action": "tool", "name": "get_coverage", "arguments": {}},
            {"action": "tool", "name": "get_round_metrics", "arguments": {"round_id": "round-2"}},
            {
                "action": "tool",
                "name": "evaluate_scenario",
                "arguments": {"activity_ids": ["kernel-a-1", "kernel-b-1"], "speedup": "2"},
            },
            {
                "action": "finish",
                "hypotheses": [
                    {
                        "text": "MOCK: inspect repeated context before tuning. "
                        "This scripted answer is not a real-model diagnosis.",
                        "evidence_refs": [],
                        "metric_refs": ["synthetic-three-rounds/tokens/input_tokens"],
                    }
                ],
                "next_checks": [
                    "Collect a real Agent trace and explicitly configure a model endpoint."
                ],
            },
        ]
    )

    def provider(
        config: AgentConfig, messages: list[dict[str, str]], timeout: float
    ) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": json.dumps(next(actions))}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

    return provider
