# a2a_completion_notifier

This page describes how the A2A completion notifier behaves with its default
configuration.  The defaults are dev mode, minimal configuration, and no
signing keys.  Step-by-step examples demonstrate the full loop end to end.

The notifier can be customized.  Sender authentication and signing are
described later in this page.

> **Need the operational details?** See the
> [customization](a2a_completion_notifier_reference.md) page.  For diagrams,
> Store mechanics, and troubleshooting, see the
> [troubleshooting & internals](a2a_completion_notifier_plumbing.md) page.

## Assumptions

This guide assumes two LangGraph Agent Server deployments, a supervisor and a
subagent, and a small FastAPI process that can run next to the supervisor.
Install the bite:

```bash
uv add "langshark-bites[a2a-notifier]"
```

## Full setup with default configuration

The default configuration uses four components.  Each component is a single,
named step.  If any component is omitted, the supervisor is never informed that
a subagent finished.  The sections after this one describe how to customize
the setup; they are not standalone instructions.

All steps run in dev mode.  `A2A_VERIFY_MODE=dev` is the default, so no additional
configuration is required.

### Step 1 — Emit: notifier middleware on the subagent graph

Add the push middleware to your subagent graph factory.  When a subagent run
reaches a terminal state, this middleware POSTs the A2A completion notification
to the receiver's webhook.

```python
from langshark_bites.a2a_completion_notifier.middleware import build_a2a_notifier_from_config
from langshark_bites.a2a_completion_notifier.push_client import PushClient


def make_graph(config):
    return create_agent(
        model,
        tools,
        middleware=[
            build_a2a_notifier_from_config(config, push_client=PushClient()),
        ],
    )
```

### Step 2 — Receive: receiver sidecar next to the supervisor

When the supervisor asks a subagent to do work, the subagent runs on a separate
deployment.  The Agent Server does not provide a push channel through which the
subagent can report completion, so a receiver is required to accept that
notification.

The receiver is a small process placed next to the supervisor.  It performs
three functions:

1. accepts the subagent's completion notification through the webhook;
2. routes the notification to the correct supervisor conversation;
3. writes the completion into the prompt history, where the supervisor's model
   sees it on its next turn.  The drain middleware in Step 3 reads that write.

Run the FastAPI receiver as a small sidecar process:

```python
import uvicorn
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

app = create_receiver_app(settings=ReceiverSettings.from_env())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=ReceiverSettings.from_env().receiver_port)
```

Or, when the module is importable, the uvicorn CLI form works just as well:

```bash
uvicorn a2a_receiver:app --host 0.0.0.0 --port 8001
```

You own this process (orchestrator/compose); the lifecycle caveats are in the
[customization](a2a_completion_notifier_reference.md).

### Step 3 — Drain: drain middleware on the supervisor graph

Add the drain middleware to the supervisor graph.  At the start of each model
call it reads pending completion notices for the thread from the Store and
injects them as a single message.  This is how the completion reaches the
supervisor's model.

```python
from langshark_bites.a2a_completion_notifier.drain import MailboxDrainMiddleware

supervisor = create_agent(model, tools, middleware=[MailboxDrainMiddleware()])
```

When a completion arrives for the current thread, the drain injects a block
into the prompt history.  One `[SUBAGENT COMPLETION NOTICE]` block is written
per finished subagent task; several notices arriving together are coalesced
into one message:

```
[SUBAGENT COMPLETION NOTICE]
task_id: dispatch_1_abc123
status: completed
summary: Researched LangGraph store namespaces; key finding in §04.
```

The notice text is produced by `render_notice(task_id, value)`.  The
`summary` is the subagent's final message summary, and `status` is the terminal
run state, such as `completed` or `failed`.  A failed task is notified in the
same way, so the supervisor does not wait indefinitely for a result.

### Step 4 — Dispatch: pass the push config when you run a subagent

When the supervisor dispatches work to the subagent, pass a push config so the
subagent knows *where* to notify and *which* supervisor thread/assistant the
completion belongs to.

```python
from langshark_bites.a2a_completion_notifier.push_config import build_push_config

config = build_push_config(
    thread_id="thread_1",
    assistant_id="supervisor_asst",
    dispatch_id="dispatch_1",
    receiver_url="https://receiver.example",  # public receiver base URL
    callback_token_secret="supervisor-secret",  # set A2A_CALLBACK_TOKEN_SECRET for the receiver
)
# await lg.runs.create(thread_id=..., assistant_id=..., input=..., config=config)
```

The complete flow is: the subagent finishes, Step 1 emits the notification,
Step 2 receives it and writes the completion to the Store, Step 3 drains the
notice into the next model call.  When a later section says it "upgrades step
N", it changes exactly one of these four pieces.

## Components

> **Each component below assumes the four-step default setup is in place and
> shows only the piece it changes.**  None of these is a standalone setup; the
> other three steps must already be wired for the component to do anything.

### Component — Supervisor drain + system prompt (extends Step 3)

The drain in Step 3 injects raw completion notices.  The supervisor's system
prompt can define how the model should act on a notice:

`DEFAULT_WAKE_INPUT` is not involved here.  The wake message from Step 2 only
starts the run; this prompt keys off the drain's injected
`[SUBAGENT COMPLETION NOTICE]` text, which is the same regardless of what the
wake message says.

```python
supervisor_system_prompt = """\
When you see a [SUBAGENT COMPLETION NOTICE]:
1. If the summary fully answers the sub-task, continue without fetching.
2. Otherwise call get_async_result(task_id) once -- never poll, never re-fetch.
3. Use the returned result and continue.
"""
```

The `get_async_result` tool it references is wired by the next component.

### Component — Fetching the full result (optional add-on to Step 3)

The completion notice carries only a short summary.  When the supervisor needs
the full output, fetch it on demand (no polling):

```python
from langshark_bites.a2a_completion_notifier.result_fetch import (
    ResultFetchSettings,
    fetch_task_result,
)

full = await fetch_task_result(
    task_id,
    settings=ResultFetchSettings(subagent_url="http://subagent-server:8000"),
    http=your_http_client,  # httpx.AsyncClient
)
```

Wire `fetch_task_result` into a tool on the supervisor graph, or call it
directly against the subagent Agent Server.

## Webhook body: sparse A2A Task

The completion POST body is an official **A2A `Task`** (proto JSON via
`a2a-sdk`), not a bespoke dict.  Only the fields needed for a terminal
completion are populated:

```json
{
  "id": "<task id>",
  "contextId": "<optional supervisor thread / A2A context>",
  "status": { "state": "TASK_STATE_COMPLETED" },
  "metadata": {
    "token": "<opaque callback token>",
    "summary": "optional short text"
  }
}
```

`metadata.token` is still the supervisor-minted routing token (extension).
Push-config registration remains the existing dispatch-time polyfill
(`url` + `token` + optional `context_id`); A2A push-config CRUD is separate.

## Recognizing notifier messages in the supervisor chat

When a subagent finishes, the notifier places two kinds of messages into the
supervisor chat.  Neither message originates from the user.

Nothing visually marks these messages as injected.  They arrive as
ordinary-looking user turns; there is no badge, color, or prefix flag.
Depending on the chat UI, the `[user]` label may not be visible.  The way to
recognize them is by content pattern, not by appearance.

The following shows a supervisor chat after one notification has been
delivered.  Three messages are shown, with no annotations or markers:

```
[user]   Original request: "Research LangGraph store namespaces."

[user]   [completion notifier] wake: check for pending subagent completion notices and respond.

[user]   [SUBAGENT COMPLETION NOTICE]
         task_id: dispatch_1_abc123
         status: completed
         summary: Researched LangGraph store namespaces; key finding in §04.
```

The `[ai]` reply that follows is the supervisor's own response.

**Message ① — the wake message**

This message always starts with `[completion notifier] wake:`.  It is a short,
generic request that appears immediately before a notice.  It carries no
content.  Its purpose is to make the supervisor run once a subagent has
finished; instructions for how to respond are in each paragraph of this document.

**Message ② — the completion notice**

This message is the one the supervisor acts on.  It is recognized by the
all-caps `[SUBAGENT COMPLETION NOTICE]` header and its three labeled fields:

- `task_id:` identifies the subagent task that finished
- `status:` reports how the task ended
- `summary:` provides a short digest of the result

A failed task shows `status: failed` instead of `completed`, so the supervisor
does not wait for a result that will never arrive.

The [Supervisor drain + system prompt](#component-supervisor-drain-system-prompt-extends-step-3)
component describes how to teach the supervisor to act on a notice.

| Look for | What it is | What it means |
|---|---|---|
| `[completion notifier] wake:` … | wake message | a subagent finished; a completion notice will follow |
| `[SUBAGENT COMPLETION NOTICE]` + `task_id:` / `status:` / `summary:` | completion notice | a task finished or failed; the supervisor acts on the result |

## Next steps

- **[Customization](a2a_completion_notifier_reference.md)** — packaging
  options, `A2A_VERIFY_MODE`, receiver lifecycle, env vars, HTTP/error table, and the
  API reference.
- **[Troubleshooting & internals](a2a_completion_notifier_plumbing.md)** — diagrams
  (sequence, deployment, Store/drain), module topology, and troubleshooting.
- Nothing to run?  `examples/a2a_completion_notifier.py` is a self-contained
  end-to-end walkthrough (fakes, no network) of the full 4-component setup.
