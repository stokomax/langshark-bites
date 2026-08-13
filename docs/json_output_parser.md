# json_output_parser

Parse free-text JSON from models that reject `response_format` (e.g. DeepSeek thinking mode).

## The problem

When you use [`create_agent(response_format=...)`](https://docs.langchain.com/oss/python/langchain/agents/create_agent), the model returns validated Pydantic models. But some models, such as DeepSeek in reasoning mode, reject all forms of `response_format`. They output free-text JSON in the message content instead, often with reasoning noise before the actual output.

## How this bite helps

`extract_structured_from_messages` scans the last AI message for JSON, repairs malformed or truncated JSON, and validates it against your Pydantic schema. It handles reasoning noise, markdown code fences, and token-limit truncation.

## What topologies it supports

- [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents/create_agent) with `response_format`, where the model may reject it.
- Any agent that needs structured output from a model that will not honor `response_format`.
- DeepSeek thinking or reasoning mode, where the model embeds JSON in its message content.


## Example

See [examples/json_output_parser.py](https://github.com/stokomax/langshark-bites/blob/main/examples/json_output_parser.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/json_output_parser.py
```

## API reference

::: langshark_bites.json_output_parser
