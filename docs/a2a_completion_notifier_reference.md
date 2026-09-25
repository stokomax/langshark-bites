# a2a_completion_notifier — customization

Customization guide for the bite: sender-auth modes, the receiver lifecycle,
configuration, and the full API surface.

> New here?  Start with the
> [main overview](a2a_completion_notifier.md).  For internals & troubleshooting
> see the [troubleshooting & internals](a2a_completion_notifier_plumbing.md) page.

## Receiver packaging

The receiver runs as a standalone FastAPI process (`create_receiver_app`),
started from your own entry point (`uvicorn` or equivalent) and managed by
your orchestrator or compose setup, always-on next to the supervisor.

## Sender authentication modes (`A2A_VERIFY_MODE`)

The receiver's sender-verification posture is selected by the supervisor via
`A2A_VERIFY_MODE` on `ReceiverSettings` (default `dev`).  In every mode the routing
callback token is mandatory; the notification is rejected with 400 if
`metadata.token` cannot be unsealed.  The mode only governs *sender-identity*
verification:

| Mode | Callback-token secret | RSA signing key + JWKS | Behaviour |
|---|---|---|---|
| `dev` | ✅ required | ❌ not needed | Trusted private network; accepts signed or unsigned.  Logs `a2a_dev_ignores_sender_jwt`. |
| `verify` | ✅ required | ✅ required (JWKS URL + issuer) | Verifies signed notifications (failing closed to 401 when a signature is bad), tolerates unsigned (logged).  Good mid-migration from dev. |
| `strict` | ✅ required | ✅ required | Rejects unsigned (401); verifies every signed notification.  Requires `A2A_SUBAGENT_JWKS_URL` + `A2A_SUBAGENT_ISSUER`. |

`dev` and `verify` log a warning if you configure sender-auth settings they will
ignore (or if `verify` has no JWKS/issuer to verify against).

## Receiver lifecycle

Notification request lifecycle, in order:

1. Unseal the callback token: the routing invariant, enforced in every mode.
2. Sender verification, dispatched by `A2A_VERIFY_MODE`.
3. Claim the dedup key exactly once (JWT `jti`, else the payload `id`).
4. Accept only terminal states (`completed` / `failed` / ...).
5. `taskId` inside the JWT must match the payload `id` (signed only).
6. ACK `202` (`BackgroundTasks`), then Store write + wake.

## Wake-up input (`DEFAULT_WAKE_INPUT` / `wake_input`)

After the receiver writes the completion to the supervisor's Store it asks the
Agent Server for a **wake-up run** (`NotificationMailbox.trigger_if_idle`) so the
drained notice reaches a model call.  By default the wake run carries the real
user message `DEFAULT_WAKE_INPUT` rather than an empty input:

- On an idle (non-interrupted) thread, a run started with `input=None` is a
  silent no-op in some langgraph-api versions (no node executes, so the
  `before_model` drain never fires and the supervisor never wakes).
- The minimal wake message makes the graph run.  The
  `MailboxDrainMiddleware` injects the completion notice itself.  The model
  does see the wake message as a user turn, and only to make the run execute;
  the notice injected by the drain is what the supervisor should act on, and
  the supervisor prompt that defines that behavior (drain step) keys off the
  injected notice, not this wake text.  Overriding `wake_input` with
  deployment-specific text is safe.

```python
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

# Default: every wake run carries DEFAULT_WAKE_INPUT (a minimal wake message).
app = create_receiver_app(settings=ReceiverSettings.from_env())

# Opt-out: resume/interrupt flows use a truly input-less wake run (original behavior).
app = create_receiver_app(settings=ReceiverSettings.from_env(), wake_input=None)

# Custom wake input: any graph input shape the supervisor accepts.
app = create_receiver_app(
    settings=ReceiverSettings.from_env(),
    wake_input={"messages": [{"role": "user", "content": "check for completions"}]},
)
```

The same `wake_input` parameter is available on `NotificationMailbox` directly.

## Requirements & env vars

Install the bite (the `[a2a-notifier]` extra pulls `fastapi`, `uvicorn`, `a2a-sdk`):

```bash
uv add "langshark-bites[a2a-notifier]"
```

Key `A2A_*` env vars (resolved by `from_env()`; default shown where relevant):

| Env var | Purpose | Default |
|---|---|---|
| `A2A_SUPERVISOR_URL` | Supervisor Agent Server base URL | `http://localhost:8000` |
| `A2A_SUPERVISOR_API_KEY` | Supervisor API key | – |
| `A2A_CALLBACK_TOKEN_SECRET` | Secret for minting/unsealing callback tokens | – |
| `A2A_RECEIVER_URL` | Public receiver base URL (used as JWT `aud`) | `http://localhost:8001` |
| `A2A_RECEIVER_HOST` / `A2A_RECEIVER_PORT` | Receiver bind address | `0.0.0.0` / `8001` |
| `A2A_VERIFY_MODE` | Sender-auth posture (`dev` / `verify` / `strict`) | `dev` |
| `A2A_SUBAGENT_JWKS_URL` | Subagent JWKS endpoint (`verify`/`strict`) | – |
| `A2A_SUBAGENT_ISSUER` | Pin the sender `iss` claim (`verify`/`strict`) | – |
| `A2A_SUBAGENT_URL` | Subagent Agent Server base (full-result fetch) | – |
| `A2A_SUBAGENT_API_KEY` | Bearer token for the subagent deployment | – |
| `A2A_REDIS_URL` | Redis URL used for cross-replica `jti` dedup (via `RedisJtiStore`) | – (in-memory default) |

> **Delivery-failure status:** on a Store/wake failure the receiver logs
> `a2a_forward_failed` / `a2a_forward_wake_failed` at error level (surface,
> not silent) but does not park the notification anywhere for later
> inspection.  Treat those log events as the delivery-failure signal.

Emitter-side env (subagent deployment): `A2A_PRIVATE_KEY_PEM` (or
`A2A_PRIVATE_KEY_FILE`), `A2A_KID`, `A2A_EMITTER_ISSUER`, `A2A_EMITTER_AUDIENCE`,
and the retry policy (`A2A_TIMEOUT_SECONDS`, `A2A_MAX_RETRIES`,
`A2A_RETRY_BACKOFF_SECONDS`).

## HTTP / error surface

What the receiver can return, and what to check when it does.

| Response | Meaning | What to check |
|---|---|---|
| `202` `{"status":"accepted"}` | Notification accepted; Store write + wake run in background | – |
| `202` `{"status":"duplicate"}` | `jti` already claimed (at-least-once redelivery) | `A2A_REDIS_URL` wiring; `jti` TTL vs sender retry window |
| `400` | Callback token invalid/expired, or non-terminal state | `A2A_CALLBACK_TOKEN_SECRET` mismatch; token TTL; `status.state` |
| `401` | Bad/missing sender JWT, unknown `kid`, stale `iat` | JWKS URL, `iss`/`aud` pinning, `A2A_VERIFY_MODE`, `iat_staleness_seconds` |
| `502` | JWKS/key fetch failed | Subagent JWKS endpoint reachable; response is valid JSON |

Error cases inside the bite log structlog events (see the
[troubleshooting page](a2a_completion_notifier_plumbing.md) for the full table and the
alert-worthy log events).

## API reference

Grouped by role; each section lists the public symbols of the modules that
implement it.

### Emitter — subagent server side

Sent by the subagent deployment: the middleware that fires the webhook, the
RS256 signer, and the HTTP delivery client.

::: langshark_bites.a2a_completion_notifier.middleware
    options:
      heading_level: 4

::: langshark_bites.a2a_completion_notifier.signer
    options:
      heading_level: 4

::: langshark_bites.a2a_completion_notifier.push_client
    options:
      heading_level: 4

### Supervisor — dispatch

Minted by the supervisor when it dispatches work to a subagent.

::: langshark_bites.a2a_completion_notifier.push_config
    options:
      heading_level: 4

### Receiver — inbound webhook

Terminates the A2A POST, verifies the sender, and deduplicates.

::: langshark_bites.a2a_completion_notifier.receiver
    options:
      heading_level: 4

::: langshark_bites.a2a_completion_notifier.auth
    options:
      heading_level: 4

::: langshark_bites.a2a_completion_notifier.idempotency
    options:
      heading_level: 4

### Receiver — delivery (Store write + drain)

Writes completions into the supervisor's Store and injects them into the next
model call.

::: langshark_bites.a2a_completion_notifier.mailbox
    options:
      heading_level: 4

::: langshark_bites.a2a_completion_notifier.drain
    options:
      heading_level: 4
      filters: ["!^_", "!^log$", "!^RenderNotice$", "!^_DEFAULT_RENDER_NOTICE$"]

### Supervisor — full-result fetch

Fetches a completed subagent task's full result on demand (the sister
primitive).

::: langshark_bites.a2a_completion_notifier.result_fetch
    options:
      heading_level: 4

### Wire format and routing tokens (shared)

The notification body contract and the opaque callback token that both sides
use.

::: langshark_bites.a2a_completion_notifier.payload
    options:
      heading_level: 4

::: langshark_bites.a2a_completion_notifier.tokens
    options:
      heading_level: 4

### Settings and security modes

Both deployments' configuration and the ``A2A_VERIFY_MODE`` ladder.

::: langshark_bites.a2a_completion_notifier.settings
    options:
      heading_level: 4

### Supervisor consumption

Build an MCP server's tools as reconnectable native LangChain tools for the
supervisor graph.  Uses a short-lived discovery scope so tools reconnect per
invocation (never hold the adapter across runs).

::: langshark_bites.a2a_completion_notifier.supervisor_tools
    options:
      heading_level: 4

