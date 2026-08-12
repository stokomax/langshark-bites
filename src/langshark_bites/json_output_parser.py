"""Shared utility to extract structured output from agent messages.

When ``create_agent(response_format=...)`` is used, the model returns
validated Pydantic models via ``state["structured_response"]``.  However,
DeepSeek's reasoning/thinking mode rejects all forms of ``response_format``
(tool_choice, json_schema, json_object).  In that case the model outputs
free-text JSON in its message content, and this utility parses it.

Usage::

    from langshark_bites.json_output_parser import (
        extract_structured_from_messages,
    )

    content = state.get("structured_response")
    if content is None:
        content = extract_structured_from_messages(
            state.get("messages", []), MySchema
        )
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pydantic import BaseModel

log = structlog.get_logger(__name__)

# Maximum debug-logged snippet length for failed JSON (characters)
_MAX_DUMP_LEN = 600


def _extract_all_json_objects(text: str, include_unclosed: bool = False) -> list[str]:
    """Extract *all* top-level JSON values (object or array) from *text*.

    Uses a character-by-character brace/bracket depth scanner that correctly
    handles arbitrary nesting, string literals (including escaped quotes), and
    markdown code fences (`````json ... `````).  This is strictly more reliable than
    a non-greedy regex ``\\{.*?\\}`` which truncates on the first ``}`` inside
    a nested structure.

    Args:
        text: The text to scan.
        include_unclosed: When True, a trailing unterminated container (e.g.
            the model's output was truncated mid-JSON by a token limit) is
            appended as the final list element so the caller can attempt
            repair (``json_repair`` can auto-close open braces/strings).

    Returns a list of raw JSON substrings in order of appearance, or an
    empty list if no top-level JSON container is found.  Only balanced
    (complete) containers are returned unless *include_unclosed* is set.
    """
    results: list[str] = []
    start = -1
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if start == -1:
            if ch in ("{", "["):
                start = i
                if ch == "{":
                    depth_brace = 1
                else:
                    depth_bracket = 1
            continue

        # We are inside the top-level container
        if ch == '"':
            in_string = True
            escape = False
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1

        if depth_brace == 0 and depth_bracket == 0:
            results.append(text[start : i + 1])
            # Reset to scan for further top-level containers
            start = -1
            depth_brace = 0
            depth_bracket = 0

    if start != -1:
        fragment = text[start:]
        log.warning(
            "structured_fallback_unclosed_json",
            start_char=text[start],
            fragment_len=len(fragment),
            tail_snippet=fragment[-_MAX_DUMP_LEN:],
        )
        if include_unclosed:
            results.append(fragment)

    return results


def _extract_json_object(text: str) -> str | None:
    """Extract the *first* top-level JSON value (object or array) from *text*.

    Convenience wrapper around :func:`_extract_all_json_objects`.
    """
    results = _extract_all_json_objects(text)
    return results[0] if results else None


def extract_structured_from_messages(
    messages: list[dict[str, Any]],
    schema_cls: type[BaseModel],
) -> BaseModel | None:
    """Parse the last AI message's content as JSON and validate against *schema_cls*.

    Args:
        messages: The agent's message list from ``state["messages"]``.
        schema_cls: A Pydantic model class to validate against.

    Returns:
        An instance of *schema_cls* if parsing and validation succeed,
        ``None`` otherwise.
    """
    if not messages:
        log.warning("structured_fallback_no_messages")
        return None

    # Walk backwards to find the last AI message with content
    for msg in reversed(messages):
        content = _get_message_content(msg)
        if content:
            break
    else:
        log.warning("structured_fallback_no_ai_content")
        return None

    # Extract all top-level JSON objects — the LLM may embed intermediate JSON
    # (e.g. ChartSpec inline in reasoning) before the final WorkerOutput.
    # include_unclosed=True: a truncated final JSON (token-limit cut-off) is
    # appended as the last candidate and tried FIRST (reverse iteration) so
    # json_repair can salvage it before falling back to earlier blobs.
    raws = _extract_all_json_objects(content, include_unclosed=True)
    if not raws:
        log.warning(
            "structured_fallback_no_json_block",
            content_snippet=content[-_MAX_DUMP_LEN:],
        )
        return None

    # Try each extracted JSON blob: parse, repair if needed, validate.
    # Return the first that validates successfully against the schema.
    #
    # Two heuristics to handle DeepSeek reasoning/thinking output:
    #   1. Skip non-dict candidates — bare arrays like ["4951", "4952"] are
    #      intermediate reasoning artifacts, not the final WorkerOutput.
    #   2. Iterate in reverse — the model's final output is almost always at
    #      the end of the message, after all intermediate reasoning.
    errors: list[str] = []
    for raw in reversed(raws):
        # First pass: stdlib json.loads
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Second pass: json_repair handles common LLM defects
            try:
                import json_repair  # type: ignore[import-untyped]

                data = json_repair.repair_json(raw, return_objects=True)
            except Exception as repair_exc:
                errors.append(f"repair_failed: {repair_exc}")
                continue

        # Skip non-dict candidates — bare arrays are intermediate reasoning
        # artifacts, not the final structured output.
        if not isinstance(data, dict):
            errors.append(f"skipped_non_dict: {type(data).__name__}")
            continue

        try:
            return schema_cls.model_validate(data)
        except Exception as exc:
            errors.append(str(exc))
            continue

    # All JSON blobs failed — log the first snippet for debugging
    log.warning(
        "structured_fallback_validation_failed",
        error=errors[0] if errors else "unknown",
        raw_snippet=raws[0][:_MAX_DUMP_LEN],
        num_candidates=len(raws),
    )
    return None


def _get_message_content(msg: Any) -> str | None:
    """Extract text content from a message, handling both ``AIMessage`` objects and dicts.

    ``state["messages"]`` contains ``AIMessage`` Pydantic objects (not plain
    dicts), so we must handle both shapes gracefully.
    """
    # Handle AIMessage / BaseMessage objects
    if hasattr(msg, "type") and hasattr(msg, "content"):
        msg_type = msg.type
        # BaseMessage.type can be "ai", "assistant", "human", "system", etc.
        if msg_type not in ("ai", "assistant"):
            return None
        content = msg.content
        if isinstance(content, str) and content.strip():
            return content
        # Handle list-of-blocks format (e.g. Anthropic tool-use messages)
        if isinstance(content, list):
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            combined = "".join(texts).strip()
            return combined if combined else None
        return None

    # Handle plain dicts
    role = msg.get("role", "")
    if role != "assistant":
        return None

    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content

    # Handle list-of-blocks format (e.g. Anthropic tool-use messages)
    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        combined = "".join(texts).strip()
        return combined if combined else None

    return None
