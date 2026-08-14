"""Tests for langshark_bites.state_reducers.envelope_reducer."""

from __future__ import annotations

from langshark_bites.state_reducers import envelope_reducer


class TestEnvelopeReducer:
    def test_empty_existing(self):
        """New entries are appended when there is no existing state."""
        new = [{"envelope_id": "a", "worker": "w1", "status": "ok"}]
        assert envelope_reducer([], new) == new

    def test_upsert_by_envelope_id(self):
        """An update to an existing envelope_id merges in place, not duplicate."""
        existing = [{"envelope_id": "a", "worker": "w1", "status": "ok", "persisted": False}]
        new = [{"envelope_id": "a", "worker": "w1", "status": "ok", "persisted": True}]
        result = envelope_reducer(existing, new)
        assert len(result) == 1
        assert result[0]["envelope_id"] == "a"
        assert result[0]["persisted"] is True

    def test_later_entries_win_field_wise(self):
        """Later entries win field-wise on merge."""
        existing = [{"envelope_id": "a", "worker": "w1", "status": "ok", "summary": "old"}]
        new = [{"envelope_id": "a", "worker": "w1", "status": "ok", "summary": "new"}]
        result = envelope_reducer(existing, new)
        assert result[0]["summary"] == "new"

    def test_preserves_structured_when_update_omits_it(self):
        """structured is preserved from existing when the update doesn't set it."""
        existing = [
            {"envelope_id": "a", "worker": "w1", "structured": {"x": 1}, "persisted": False}
        ]
        new = [{"envelope_id": "a", "worker": "w1", "persisted": True}]
        result = envelope_reducer(existing, new)
        assert result[0]["structured"] == {"x": 1}
        assert result[0]["persisted"] is True

    def test_explicit_structured_none_is_kept(self):
        """Explicit structured=None (post-persist shrink) is kept."""
        existing = [
            {"envelope_id": "a", "worker": "w1", "structured": {"x": 1}, "persisted": False}
        ]
        new = [{"envelope_id": "a", "worker": "w1", "structured": None, "persisted": True}]
        result = envelope_reducer(existing, new)
        assert result[0]["structured"] is None
        assert result[0]["persisted"] is True

    def test_distinct_envelope_ids_accumulate(self):
        """Distinct envelope_ids accumulate in first-seen order."""
        existing = [{"envelope_id": "a", "worker": "w1"}]
        new = [{"envelope_id": "b", "worker": "w2"}]
        result = envelope_reducer(existing, new)
        assert [e["envelope_id"] for e in result] == ["a", "b"]

    def test_fallback_key_worker_as_of(self):
        """Entries without envelope_id fall back to worker:as_of."""
        existing = [{"worker": "w1", "as_of": "2026-01-01", "status": "ok"}]
        new = [{"worker": "w1", "as_of": "2026-01-01", "status": "ok", "persisted": True}]
        result = envelope_reducer(existing, new)
        assert len(result) == 1
        assert result[0]["persisted"] is True

    def test_insertion_order_preserved(self):
        """Order is insertion order of first-seen envelope_ids."""
        existing = [{"envelope_id": "a"}, {"envelope_id": "b"}]
        new = [{"envelope_id": "c"}, {"envelope_id": "a", "status": "updated"}]
        result = envelope_reducer(existing, new)
        assert [e["envelope_id"] for e in result] == ["a", "b", "c"]
        assert result[0]["status"] == "updated"
