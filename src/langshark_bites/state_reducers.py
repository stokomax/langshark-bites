"""LangGraph state reducers for multi-agent accumulation.

When ``Send`` fan-out dispatches parallel workers, their results merge back
into the shared graph state in non-deterministic order.  Plain ``operator.add``
would duplicate rows whenever a later node updates an existing entry (e.g. a
persist node flipping ``persisted=True`` on an already-collected result).

``envelope_reducer`` upserts result entries by a stable key (``envelope_id``,
falling back to ``worker:as_of``) so updates merge in place instead of
appending duplicates.

Usage in a LangGraph state schema::

    from typing import Annotated
    from langshark_bites.state_reducers import envelope_reducer

    class State(TypedDict):
        collected_outputs: Annotated[list[dict], envelope_reducer]

Consumers MUST key by ``envelope_id`` and never by list position — the
returned list order is insertion order of first-seen keys and is not
meaningful.
"""

from __future__ import annotations

from typing import Any


def envelope_reducer(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Upsert result envelopes by ``envelope_id``.  Later entries win field-wise.

    Replaces plain ``operator.add`` so persist nodes can update
    ``persisted=True`` (and drop ``structured``) without duplicating rows.
    Order of the returned list is insertion order of first-seen envelope_ids
    and is NOT meaningful — always key by envelope_id.

    Args:
        existing: The current accumulated list in graph state.
        new: The newly produced entries to merge in.

    Returns:
        A new list with entries upserted by key.  Entries are shallow-merged
        field-wise; later entries win.  The ``structured`` payload is
        preserved from the existing entry unless the update explicitly sets
        it to ``None``.
    """
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _tid(entry: dict[str, Any]) -> str:
        tid = entry.get("envelope_id") or ""
        if tid:
            return tid
        return f"{entry.get('worker', '')}:{entry.get('as_of', '')}"

    for entry in existing:
        tid = _tid(entry)
        if tid not in by_id:
            order.append(tid)
        by_id[tid] = entry

    for entry in new:
        tid = _tid(entry)
        if tid in by_id:
            merged = {**by_id[tid], **entry}
            # Preserve structured payload if the update intentionally drops it
            # only when the update didn't set the key at all.  Explicit
            # structured=None (post-persist shrink) is kept.
            if "structured" not in entry and by_id[tid].get("structured") is not None:
                merged["structured"] = by_id[tid]["structured"]
            by_id[tid] = merged
        else:
            order.append(tid)
            by_id[tid] = entry

    return [by_id[tid] for tid in order]
