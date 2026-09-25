# AGENTS.md

Orientation for AI coding tools working in this repository.

## What this is

`langshark-bites` is a **library of independent "bites"** — small, reusable add-ons
and wrappers for durable, scalable LangChain multi-agent systems. It is not an
application. The repo root `main.py` is a hello-world remnant, **not** the entrypoint.

Each bite is a self-contained module (or small package) under `src/langshark_bites/`,
paired with tests, docs, and a runnable example:

```
src/langshark_bites/<bite>/     # bite code (a2a_completion_notifier is a package)
src/langshark_bites/<bite>.py   # single-module bites (e.g. api_rate_limiter.py)
tests/test_<bite>*.py           # pytest suite (per-bite files)
docs/<bite>.md                  # mkdocs page (built to GitHub Pages)
examples/<bite>.py              # runnable example
```

Bites currently shipped:

- `a2a_completion_notifier` — A2A push completion notifications across split Agent
  Server deployments (emitter middleware + FastAPI receiver + Store/drain + MCP).
- `api_backoff` — observable backoff sleep for retrying throttled external calls.
- `api_rate_limiter` — Redis-backed token-bucket rate limiter.
- `json_output_parser` — parse free-text JSON from models that reject `response_format`.
- `observability` — Phoenix/OpenInference tracing with span decorators.
- `provider_failover` — circuit breaker for exhausted LLM provider credit.
- `state_reducers` — LangGraph state reducers for multi-agent accumulation.

## Toolchain

- **Package manager:** `uv` (Python 3.14 per `.python-version` and `requires-python`).
- **Lint:** `ruff` (line-length 100; rule set F, W, E, I, C4, FA, ISC, ICN, RET, SIM,
  TID, TC, TD; first-party `langshark_bites`).
- **Tests:** `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`, `testpaths = ["tests"]`).
- **Docs:** `mkdocs` with the Material theme; API docs via `mkdocstrings`.

## Commands

```bash
uv run pytest                          # full test suite
uv run pytest tests/test_<bite>.py     # one bite's tests
uv run ruff check                      # lint (all files)
uv run ruff check --fix                # auto-fix lint
uv run mkdocs build --strict           # build docs (strict: warnings fail)
uv run python examples/<bite>.py       # run a bite's example
uv sync --group dev                    # install dev tooling
uv sync --group docs                   # install docs tooling
```

Note: the `a2a_completion_notifier` examples and tests require the `a2a-notifier`
extra (`uv add "langshark-bites[a2a-notifier]"` locally: `fastapi`, `mcp[cli]`,
`uvicorn[standard]`).

## Conventions

- **One module per bite.** Single-module bites live directly in
  `src/langshark_bites/`; the a2a notifier is a subpackage.
- **Public API is re-exported** from the bite's `__init__.py` / the package root
  `__init__.py` (`__all__` maintained alphabetically).
- **Docstrings open with a "Why this exists" section**, then Usage/Args. New code
  follows the same pattern.
- **Logging uses `structlog`** (`log = structlog.get_logger(__name__)`).
- **`TYPE_CHECKING` imports** for type-only names to keep runtime imports light.
- Settings are resolved from `*_env()`-style helpers or dataclass `from_env()`
  classmethods (uppercase, `_`-separated, bite-prefixed env vars).

## Done-criteria

Do not claim a change complete until:

1. `uv run pytest` passes (or the targeted bite suite when scoping is intentional).
2. `uv run ruff check` is clean on the changed files.
3. For doc changes: `uv run mkdocs build --strict` succeeds.
4. For a bite with an example: `uv run python examples/<bite>.py` runs end-to-end.

## Extending the repo (adding or changing a bite)

- The authoring procedure lives in `skills/langshark-bites/SKILL.md` — read it
  before adding a bite or extending an existing one.
- Concrete skeletons are in `templates/bite/` (module + test + docs + example):
  copy the four files, substitute the placeholders, and rename to the bite's
  destination paths. `templates/bite/README.md` has the substitution table.
- Every bite is the quartet: `src/langshark_bites/<bite>.py` (or `<bite>/`
  package) + `tests/test_<bite>.py` + `docs/<bite>.md` + `examples/<bite>.py`.