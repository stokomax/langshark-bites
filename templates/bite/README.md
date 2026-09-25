# Bite template

Copy these four files when adding a new bite to `langshark-bites`. Replace
the placeholders and rename each file to its destination under the repo's
bite convention (`<bite>` = the bite name, e.g. `api_backoff`):

| Template file | Destination |
|---|---|
| `module_template.py` | `src/langshark_bites/<bite>.py` (or `src/langshark_bites/<bite>/__init__.py` for a package) |
| `test_template.py` | `tests/test_<bite>.py` |
| `docs_template.md` | `docs/<bite>.md` |
| `example_template.py` | `examples/<bite>.py` |

## Placeholders to substitute

- `<bite_name>` — the bite name (snake_case, e.g. `api_backoff`).
- `<public_func>` — the name of the bite's primary public function or class.
- `<BITE_NAME>` — the bite name in prose.

> [!NOTE] The example uses an `async def main()` + `asyncio.run(...)` even for
> synchronous bites, to match the repo's example convention (`examples/backoff.py`).

## Rules the template encodes

- Module docstring opens with a **"Why this exists"** section, then Usage.
- `structlog` for logging (`log = structlog.get_logger(__name__)`).
- Function docstrings cover Args / Returns / Raises.
- Tests use explicit `@pytest.mark.asyncio` for async cases (repo convention,
  even though `asyncio_mode = "auto"`).
- Doc page structure: problem → how it helps → topologies → example → API
  reference (`::: langshark_bites.<bite>`).

## Using the skill

For the full authoring procedure, read `skills/langshark-bites/SKILL.md`.