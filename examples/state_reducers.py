"""Runnable example for the state_reducers bite.

This example shows how to wire envelope_reducer into a LangGraph state schema
so parallel workers merge their results without duplicating rows. It uses a
plain function call to demonstrate the reducer behavior, so it runs without
building a full graph.

Run with:

    uv run python examples/state_reducers.py

See the docs: https://stokomax.github.io/langshark-bites/state_reducers/
"""

from __future__ import annotations

from langshark_bites.state_reducers import envelope_reducer


def main() -> None:
    # State accumulated so far. A persist node has already marked one entry
    # as persisted.
    existing = [
        {"envelope_id": "a", "worker": "w1", "status": "ok", "persisted": False},
        {"envelope_id": "b", "worker": "w2", "status": "ok", "persisted": True},
    ]

    # A new batch of results from a parallel worker. It updates envelope "a"
    # (marking it persisted) and adds a new envelope "c".
    new = [
        {"envelope_id": "a", "worker": "w1", "status": "ok", "persisted": True},
        {"envelope_id": "c", "worker": "w3", "status": "ok", "persisted": False},
    ]

    merged = envelope_reducer(existing, new)

    print("merged entries:")
    for entry in merged:
        print(" ", entry)

    # With a plain operator.add, envelope "a" would appear twice. The reducer
    # upserts by envelope_id, so it appears once with persisted=True.
    assert len(merged) == 3
    assert [e["envelope_id"] for e in merged] == ["a", "b", "c"]
    assert merged[0]["persisted"] is True
    print("no duplicates: envelope 'a' updated in place")


if __name__ == "__main__":
    main()
