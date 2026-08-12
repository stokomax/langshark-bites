"""Runnable example for the json_output_parser bite.

This example shows how to parse free-text JSON from an agent's messages into
a validated Pydantic model. It covers the case where a model rejects
response_format (for example DeepSeek in reasoning mode) and returns JSON
embedded in its message content, possibly with reasoning noise.

Run with:

    uv run python examples/json_output_parser.py

See the docs: https://stokomax.github.io/langshark-bites/json_output_parser/
"""

from __future__ import annotations

from pydantic import BaseModel

from langshark_bites.json_output_parser import extract_structured_from_messages


class Observation(BaseModel):
    symbol: str
    direction: str


class WorkerOutput(BaseModel):
    worker_name: str
    observations: list[Observation] = []
    cross_cutting_notes: str = ""


def main() -> None:
    # A message list as it would appear in state["messages"]. The model
    # rejected response_format and put free-text JSON in its content, with
    # reasoning noise before the final output.
    messages = [
        {
            "role": "assistant",
            "content": (
                "Let me analyze the signals...\n"
                'Chart: {"type": "line", "spec": {"ticker": "XLV", "window_days": 60}}\n'
                "Now the output:\n"
                '{"worker_name": "daily_signal_analysis", '
                '"observations": [{"symbol": "SPX", "direction": "bullish"}], '
                '"cross_cutting_notes": "all good"}'
            ),
        }
    ]

    result = extract_structured_from_messages(messages, WorkerOutput)
    if result is None:
        print("parsing failed")
        return

    print("worker_name:", result.worker_name)
    print("observations:", result.observations)
    print("cross_cutting_notes:", result.cross_cutting_notes)


if __name__ == "__main__":
    main()
