---
name: langshark-bites
description: Author and extend bites in the langshark-bites repository — the module/tests/docs/example quartet, docstring style, verification pipeline. Use when adding a new bite, extending an existing one, or fixing behavior in this repo.
---

# langshark-bites — bite authoring

Procedure for adding or extending a bite in this repo. Every bite is an
independent, self-contained add-on: module + tests + docs + example. Follow
this exactly so the result matches the repo conventions.

## When to Use This Skill

- Adding a brand-new bite to `src/langshark_bites/`.
- Extending an existing bite (new public API, new behavior, new settings).
- Fixing a bug in a bite (module, tests, or docs) and verifying it.

## Repo facts

- Layout: `src/langshark_bites/<bite>.py` (single-module) or
  `src/langshark_bites/<bite>/` (package, e.g. `a2a_completion_notifier`).
  Mirrored by `tests/test_<bite>*.py`, `docs/<bite>.md`, `examples/<bite>.py`.
- `main.py` at root is a hello-world remnant — never the entrypoint, never a
  verification path.
- Toolchain: uv (Python 3.14), ruff (line-length 100), pytest + pytest-asyncio
  (`asyncio_mode = "auto"` — async tests need no explicit mark; a few older
  tests still carry a redundant `@pytest.mark.asyncio`, don't copy that),
  mkdocs Material (`--strict`).

## The four files every bite needs

Create these four (five for a package) and no more:

| File | Purpose |
|---|---|
| `src/langshark_bites/<bite>.py` | The public API |
| `tests/test_<bite>.py` | pytest suite proving the documented API |
| `docs/<bite>.md` | mkdocs page (shipped to GitHub Pages) |
| `examples/<bite>.py` | Runnable example (done-criteria requires it run) |

> [!TIP] Use the template
> Skeleton files for all four live in `templates/bite/`. Copy `module_template.py`,
> `test_template.py`, `docs_template.md`, `example_template.py`, substitute the
> placeholders (see `templates/bite/README.md`), and rename to the destination
> paths above.

## Module skeleton (docstring-first)

```python
"""<One-line purpose>. <What it does and for whom>.

Why this exists
---------------
<The problem it solves, in 2-4 sentences. Motivate, don't restate the API.>

Usage
-----
    from langshark_bites.<bite> import <public>

    <public>(...)
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

# ...public functions/classes, each with Args/Returns/Raises docstrings...
```

Rules:
- Every function's docstring covers Args / Returns / Raises explicitly.
- `structlog` for logging (`log = structlog.get_logger(__name__)`).
- `TYPE_CHECKING` imports for type-only names.
- Environment/settings via dataclass `from_env()` classmethod (uppercase
  `_`-separated, bite-prefixed env vars).

## Adding a package bite (e.g. middleware + server)

If the bite needs several pieces, make a package and re-export everything:

```
src/langshark_bites/<bite>/__init__.py   # re-exports + alphabetical __all__
src/langshark_bites/<bite>/<part>.py     # one concern per module
```

The package `__init__.py` must re-export the public API and maintain
`__all__` alphabetically (lint-enforced adjacent to ruff's import rules).
The package root `src/langshark_bites/__init__.py` re-exports the bite, too.

## Test conventions

- File: `tests/test_<bite>.py`, one class or function per behavior area.
- Async tests: `@pytest.mark.asyncio`, and mock `asyncio.sleep` /
  real side effects with `AsyncMock` so tests are fast and deterministic.
- Test the *documented* behavior, not the implementation. If an API is in
  the docstring, it must be exercised here.
- Follow `tests/test_api_backoff.py` as the reference for a single-module bite.

## Docs skeleton

```markdown
# <bite>

<One-line summary>.

## The problem
## How this bite helps
## Example          # link to examples/<bite>.py, with `uv run python examples/<bite>.py`
## API reference
::: langshark_bites.<bite>
```

## Verification (mandatory, in order)

```bash
uv run pytest tests/test_<bite>.py     # targeted suite passes
uv run ruff check src/ tests/ examples/ # clean on changed files
uv run mkdocs build --strict           # only for docs changes
uv run python examples/<bite>.py       # only for bites with an example
```

Then run the full suite: `uv run pytest`.

## Pitfalls

- Do NOT add `main.py`-style entrypoints or app scaffolding.
- Do NOT add the full `arize-phoenix` SDK; the repo pins `arize-phoenix-otel`
  (dependency-conflict reasons documented in `pyproject.toml`).
- If a bite touches the a2a notifier, its extra deps (`fastapi`, `mcp[cli]`,
  `uvicorn[standard]`) come from `langshark-bites[a2a-notifier]` — they are
  NOT base dependencies.
- Keep `__all__` alphabetical in every `__init__.py` you touch (ruff checks).
- Never restate the problem in the docstring as if it were the solution;
  "Why this exists" motivates the module.