"""Tests for langshark_bites.json_output_parser."""

from __future__ import annotations

from pydantic import BaseModel

from langshark_bites.json_output_parser import (
    _extract_all_json_objects,
    _extract_json_object,
    extract_structured_from_messages,
)

# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------

class TestExtractJsonObject:
    def test_flat_object(self):
        """Simple flat JSON object."""
        assert _extract_json_object('{"a": 1, "b": 2}') == '{"a": 1, "b": 2}'

    def test_nested_object(self):
        """Nested object — this was the regression: old non-greedy regex
        would stop at the first ``}`` inside the nested dict."""
        raw = '{"a": {"b": 1, "c": {"d": 2}}, "e": 3}'
        result = _extract_json_object(raw)
        assert result == raw
        # Verify the extracted string is valid JSON
        import json
        parsed = json.loads(result)
        assert parsed == {"a": {"b": 1, "c": {"d": 2}}, "e": 3}

    def test_nested_array(self):
        """Object containing arrays with nested objects."""
        raw = '{"items": [{"x": 1}, {"y": 2}], "count": 2}'
        result = _extract_json_object(raw)
        assert result == raw
        import json
        assert json.loads(result) == {"items": [{"x": 1}, {"y": 2}], "count": 2}

    def test_top_level_array(self):
        """Top-level JSON array."""
        assert _extract_json_object('[1, 2, {"a": 3}]') == '[1, 2, {"a": 3}]'

    def test_string_with_braces(self):
        """String literal containing braces should not confuse the parser."""
        raw = '{"msg": "braces { like } this", "ok": true}'
        result = _extract_json_object(raw)
        assert result == raw
        import json
        assert json.loads(result) == {"msg": "braces { like } this", "ok": True}

    def test_string_with_escaped_quotes(self):
        """String with escaped quotes inside."""
        raw = '{"text": "he said \\"hello\\"", "done": false}'
        result = _extract_json_object(raw)
        assert result == raw

    def test_code_fence_wrapped(self):
        """JSON wrapped in markdown code fences."""
        raw = '```json\n{"a": 1}\n```'
        result = _extract_json_object(raw)
        # Brace-depth parser ignores the fences and finds the JSON
        assert result == '{"a": 1}'

    def test_leading_text(self):
        """Leading thinking/reasoning text before JSON."""
        raw = 'Let me think about this...\n{"result": "ok"}'
        result = _extract_json_object(raw)
        assert result == '{"result": "ok"}'

    def test_no_json(self):
        """Text with no JSON should return None."""
        assert _extract_json_object("Just some text, no braces") is None

    def test_unclosed_json(self):
        """Unclosed JSON should return None."""
        assert _extract_json_object('{"a": 1, "b": 2') is None


# ---------------------------------------------------------------------------
# _extract_all_json_objects
# ---------------------------------------------------------------------------

class TestExtractAllJsonObjects:
    def test_single_object(self):
        """Single flat object."""
        assert _extract_all_json_objects('{"a": 1}') == ['{"a": 1}']

    def test_two_flat_objects(self):
        """Two top-level objects in sequence."""
        result = _extract_all_json_objects('{"a": 1}{"b": 2}')
        assert result == ['{"a": 1}', '{"b": 2}']

    def test_leading_text_then_multiple_objects(self):
        """Thinking/reasoning text with inline JSON objects before final output."""
        text = (
            'Let me analyze the signals...\n'
            'I see a chart spec: {"type": "line", "spec": {"ticker": "XLV", "window_days": 60}}\n'
            'Now the full analysis:\n'
            '{"worker_name": "daily_signal_analysis", "observations": []}'
        )
        result = _extract_all_json_objects(text)
        assert len(result) == 2
        assert result[0] == '{"type": "line", "spec": {"ticker": "XLV", "window_days": 60}}'
        assert result[1] == '{"worker_name": "daily_signal_analysis", "observations": []}'

    def test_nested_objects_in_arrays(self):
        """Objects inside arrays should not be extracted as top-level."""
        raw = '{"items": [{"x": 1}, {"y": 2}]}'
        result = _extract_all_json_objects(raw)
        assert result == ['{"items": [{"x": 1}, {"y": 2}]}']

    def test_nested_objects_in_objects(self):
        """Nested objects should not be extracted as separate top-level."""
        raw = '{"a": {"b": {"c": 1}}}'
        result = _extract_all_json_objects(raw)
        assert result == ['{"a": {"b": {"c": 1}}}']

    def test_objects_with_braces_in_strings(self):
        """Braces inside string literals should not trigger extraction."""
        raw = '{"msg": "braces { like } this", "ok": true}{"second": 1}'
        result = _extract_all_json_objects(raw)
        assert len(result) == 2
        import json
        assert json.loads(result[0]) == {"msg": "braces { like } this", "ok": True}
        assert json.loads(result[1]) == {"second": 1}

    def test_no_json(self):
        """No JSON containers returns empty list."""
        assert _extract_all_json_objects("Just some text") == []

    def test_rejects_unclosed_first_object(self):
        """An unclosed opening JSON leaves the parser in a nested state;
        subsequent objects are not extractable."""
        text = '{"a": 1\n{"b": 2}'
        # The first "{" starts an object that never closes, so the whole text
        # is consumed and no valid top-level JSON is returned.
        result = _extract_all_json_objects(text)
        assert result == []


# ---------------------------------------------------------------------------
# extract_structured_from_messages
# ---------------------------------------------------------------------------

class ObservationModel(BaseModel):
    symbol: str
    direction: str


class WorkerOutputTest(BaseModel):
    worker_name: str  # no default — matches WorkerOutput in production
    observations: list[ObservationModel] = []
    cross_cutting_notes: str = ""


class TestExtractStructuredFromMessages:
    def test_flat_message(self):
        """Success path with a single AI message containing valid JSON."""
        messages = [
            {"role": "assistant", "content": '{"worker_name": "test", "observations": []}'}
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert result.worker_name == "test"

    def test_nested_message(self):
        """Message with nested observations — the regression case."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    '{"worker_name": "test", '
                    '"observations": [{"symbol": "SPX", "direction": "bullish"}], '
                    '"cross_cutting_notes": "nothing"}'
                ),
            }
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert len(result.observations) == 1
        assert result.observations[0].symbol == "SPX"
        assert result.observations[0].direction == "bullish"
        assert result.cross_cutting_notes == "nothing"

    def test_empty_messages(self):
        """Empty message list returns None."""
        assert extract_structured_from_messages([], WorkerOutputTest) is None

    def test_no_ai_message(self):
        """No AI messages returns None."""
        messages = [{"role": "user", "content": "hello"}]
        assert extract_structured_from_messages(messages, WorkerOutputTest) is None

    def test_invalid_json_fallback_to_repair(self):
        """Missing comma (common LLM defect) should be repaired by json_repair."""
        messages = [
            {
                "role": "assistant",
                "content": '{"worker_name": "test" "observations": []}',
            }
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert result.worker_name == "test"
        assert result.observations == []

    def test_validation_failure(self):
        """JSON that passes parse but fails Pydantic validation returns None."""
        # cross_cutting_notes should be a string, not an int
        messages = [
            {"role": "assistant", "content": '{"worker_name": "test", "cross_cutting_notes": 42}'}
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is None


class TestTruncatedJsonSalvage:
    """Token-limit truncation: the model's final JSON is cut mid-stream.

    ``include_unclosed=True`` surfaces the trailing unterminated fragment so
    ``json_repair`` can auto-close it — previously the fragment was discarded
    and persistence silently failed.
    """

    def test_include_unclosed_returns_trailing_fragment(self):
        """The unclosed fragment is appended after balanced containers."""
        text = '{"complete": true}\n{"worker_name": "test", "observations": ['
        result = _extract_all_json_objects(text, include_unclosed=True)
        assert result == [
            '{"complete": true}',
            '{"worker_name": "test", "observations": [',
        ]

    def test_include_unclosed_false_discards_fragment(self):
        """Default behaviour unchanged: unclosed fragment is dropped."""
        text = '{"complete": true}\n{"worker_name": "test"'
        result = _extract_all_json_objects(text)
        assert result == ['{"complete": true}']

    def test_salvage_truncated_mid_object(self):
        """Truncated after a complete nested value — repair closes the object."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    '{"worker_name": "test", '
                    '"observations": [{"symbol": "SPX", "direction": "bullish"}'
                ),
            }
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert result.worker_name == "test"
        assert len(result.observations) == 1
        assert result.observations[0].symbol == "SPX"

    def test_salvage_truncated_mid_string(self):
        """Truncated inside a string literal — repair closes string + object."""
        messages = [
            {
                "role": "assistant",
                "content": '{"worker_name": "test", "cross_cutting_notes": "all go',
            }
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert result.worker_name == "test"
        assert result.cross_cutting_notes.startswith("all go")

    def test_salvage_truncated_only_worker_name(self):
        """Severely truncated — only the required field survives."""
        messages = [
            {"role": "assistant", "content": 'reasoning...\n{"worker_name": "test"'}
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert result.worker_name == "test"
        assert result.observations == []

    def test_salvage_truncated_missing_required_field(self):
        """Truncated before the required field — validation must still fail."""
        messages = [
            {"role": "assistant", "content": '{"observations": [{"symbol": "SPX"'}
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is None

    def test_salvage_prefers_truncated_final_over_earlier_complete(self):
        """The truncated final output is tried before earlier complete blobs —
        an earlier complete-but-wrong JSON must not win over the real output."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    'Intermediate: {"worker_name": "wrong", "observations": []}\n'
                    'Final: {"worker_name": "test", "observations": [{"symbol": "XLV", "direction": "neutral"}'
                ),
            }
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert result.worker_name == "test"
        assert result.observations[0].symbol == "XLV"

    def test_inline_small_json_before_worker_output(self):
        """Regression: LLM reasoning includes a smaller JSON (e.g. ChartSpec) inline,
        then the final WorkerOutput.  Should skip the ChartSpec and validate the
        WorkerOutput."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    'Let me analyze the signals...\n'
                    'Chart: {"type": "line", "spec": {"ticker": "XLV", "window_days": 60}}\n'
                    'Now the output:\n'
                    '{"worker_name": "test", '
                    '"observations": [{"symbol": "SPX", "direction": "bullish"}], '
                    '"cross_cutting_notes": "all good"}'
                ),
            }
        ]
        result = extract_structured_from_messages(messages, WorkerOutputTest)
        assert result is not None
        assert result.worker_name == "test"
        assert len(result.observations) == 1
        assert result.observations[0].symbol == "SPX"
        assert result.cross_cutting_notes == "all good"
