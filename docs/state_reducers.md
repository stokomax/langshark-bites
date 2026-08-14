# state_reducers

LangGraph state reducers for multi-agent accumulation.

## The problem

When [`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send) fan-out dispatches parallel workers, their results merge back into the shared graph state in non-deterministic order. A plain [`operator.add`](https://docs.python.org/3/library/operator.html#operator.add) duplicates rows whenever a later node updates an existing entry. For example, a persist node flips `persisted=True` on an already-collected result, and now that result appears twice.

## What is `Send`?

[`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send) is a LangGraph function that lets a node dynamically dispatch multiple tasks in a single step. It is the way you express "run this same work for each of these inputs, in parallel".

Here is how [`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send) works. A node returns a list of `Send` objects, each carrying a target node (or subgraph) and an input for that target. LangGraph then schedules one task per `Send`, runs each task to completion, and merges the results back into the shared state.

```python
from langgraph.types import Send

def fan_out(state):
    # One task per unit of work, each running the "process" node.
    return [Send("process", {"key": k}) for k in state["items"]]
```

The key idea is that [`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send) is about dispatching work, not about waiting for it. You do not get a handle to wait on or a callback to register. You hand the tasks to LangGraph, and LangGraph handles the scheduling and the merge. That is why the merge step, the reducer, matters so much: it is the part that collects the results, and it has to work correctly no matter what order the tasks finish in.

## Plain function, agent, or worker? Situating this example

Before we examine the reducer, it is worth pausing to situate the example within the larger vocabulary of LangChain and LangGraph. Readers arriving from an understanding of LLMs, LangChain middleware, and `create_agent` (the deepagent builder) will encounter, in the pages that follow, three words that are often conflated: *node*, *agent*, and *worker*. They are not synonyms, and the distinction among them determines how you read the code in this bite.

### A node is a step, not a thing you write

A *node* is best understood not as an object you construct but as a *place in the graph* — a step in the orchestrated sequence. LangGraph runs a graph by moving from node to node, and each node is occupied by something that can accept the graph's state and return updates to it. Two rather different sorts of things may occupy that place: a hand-written Python function, or a compiled agent. This is the first distinction to internalize.

### The plain-function node

The `def process(state, runtime: Runtime)` signature introduced in a later section is precisely the hand-written form: a plain Python function that a node wraps. Its virtue is transparency — you can see exactly what it does, because you wrote every line of it. When we speak of the writer of such a node, we call it a *worker*.

### The agent as a node

Now consider `create_agent`, the builder behind deepagent. It composes a model, a set of tools, and the surrounding middleware — prompts, retries, tracing — into a self-driving loop. Crucially, `create_agent` returns a **compiled graph**, not a function you summon and call by hand. To make such an agent one of your graph's steps, you therefore *attach* it as a node:

```python
from langchain.agents import create_agent

builder.add_node("analyst", create_agent(model, tools, state_schema=State))
```

Observe that there is no `def process` here; the agent is self-contained. When attached in this manner, the agent constitutes a *subgraph node* — a graph nested within a graph.

### The worker is a role, not a type

Here the third term enters. A `Send` dispatches a task to a node; that node, in that role, is the *worker* of the fan-out. The worker may be a plain function or an agent; the role is indifferent to the kind. It follows that an agent is not always a worker, and a worker is not always an agent. A worker is simply any node that a `Send` targets.

### The signature, and the `envelope_id`

The `(state, runtime)` signature is a plain-function privilege: `Runtime` and its `execution_info` are injected only for hand-written nodes. An agent node does not declare this signature.

That distinction matters here for one reason: it rules out LangGraph's runtime `task_id` as the reducer's key. The runtime scopes a `task_id` to a single execution — it is unique per dispatch and only readable from a `runtime` argument. Neither property suits a key that must survive re-dispatch and hold for compiled agents alike.

The reducer keys on `envelope_id` instead: a **stable, deterministic identity** the worker derives from its own task inputs and stamps onto the envelope it returns. Because it is deterministic — the same logical unit of work always yields the same `envelope_id` — the merge is correct whether the worker is a hand-written function or a deepagent-style agent, and stays correct across retries and loops.

### A mental model

It may be useful to set out, in a compact form, the progression from the familiar to the new:

| Term | What it is |
| --- | --- |
| LLM | The model — the thing that produces text. |
| LangChain middleware | The wrappers (prompts, tools, retries, tracing) placed around a model call. |
| `create_agent` (deepagent) | Bundles model + tools + middleware into a self-driving loop; returns a compiled agent (a graph). |
| LangGraph | The orchestration layer that runs a graph of nodes. |
| Node | A step in the graph; occupied by a plain function or an agent. |
| Worker | The role of a node when a `Send` targets it — one unit of the fan-out. |

The reader who already knows LLMs, middleware, and deepagent can now perceive the central point of this bite: the merge depends on a deterministic `envelope_id`, not on any per-dispatch machinery above a single agent, and that identity works identically whether the worker is a hand-written function or a deepagent-style agent.

## How this bite helps

`envelope_reducer` upserts result entries by a stable key (`envelope_id`, falling back to `worker:as_of`). Updates merge in place instead of appending duplicates.

## Where does the `envelope_id` come from?

The reducer does not invent the `envelope_id`, and it does not read it from a `Runtime`. The worker assembles it deterministically from the task's own inputs. Given a task that names a worker and a point in time (`as_of`), the conventional scheme is:

```python
envelope_id = f"{worker}:{as_of}"
```

This is **stable**: the same worker for the same point in time always yields the same `envelope_id`. It is **unique across workers**, so parallel results do not collide. And it requires no coordination — the fan-out node, the worker, and any persist node all derive the same id from the same inputs.

### Why not LangGraph's runtime `task_id`?

LangGraph can expose a `task_id` per execution through `runtime.execution_info` (a plain-function privilege that requires declaring `runtime: Runtime`). It is tempting to read it and use it as the key, but it is the wrong tool for a *reducer* key, for two reasons:

- **It is unique per dispatch.** LangGraph assigns a fresh `task_id` every time a task runs. If the supervisor re-dispatches the same work — a retry after a provider error, or a loop that re-sends a task — the runtime assigns a *new* `task_id`. A reducer keyed on it would treat the re-run as a brand-new entry and duplicate the row, which is exactly the bug this bite prevents.
- **It is a plain-function privilege.** Reading `runtime.execution_info.task_id` requires a hand-written `(state, runtime)` signature. A compiled agent node cannot read it from a signature, so an agent-based worker could not populate the key at all.

The deterministic `envelope_id` avoids both: it is stable across re-dispatches and available to any worker, hand-written or agent.

(The plain-function `Runtime` injection still exists and still exposes `execution_info` — including a `task_id` scoped to the current execution. It is fine to record that for telemetry. It just is not the key the reducer should be keyed on.)

### Deriving it in a worker

A worker — a plain function, or a hand-written wrapper around a compiled agent — derives the `envelope_id` from the task input it received via `Send`:

```python
def worker(node_state):
    worker = node_state["worker"]
    as_of = node_state["as_of"]
    envelope_id = f"{worker}:{as_of}"  # deterministic — stable across re-dispatches

    # ... do the work ...

    return {
        "collected_outputs": [
            {
                "envelope_id": envelope_id,
                "worker": worker,
                "as_of": as_of,
                "status": "ok",
            }
        ]
    }
```

The worker stamps the `envelope_id` back into the envelope it returns, and that envelope is what flows into the shared state. That is the whole insertion point: the reducer never sees the `Send` or the runtime — it only sees the returned envelope, reads `entry.get("envelope_id")`, and keys on that.

### What the reducer requires

The reducer does not care how the `envelope_id` was assigned. It only requires that the **same logical unit of work always produces the same `envelope_id`**, so that updates to it can be recognized as the same entry. If a worker does not set `envelope_id` on an envelope, the reducer falls back to a composite key of `worker:as_of`, which is a stable identifier for a worker's output at a point in time.

## How `envelope_id` solves the ordering problem

The core difficulty is that parallel results arrive in non-deterministic order. You cannot rely on "the last result in the list is the newest". `envelope_id` gives you a way to identify which logical task a result belongs to, independent of its position in the list.

Here is how the reducer uses it. It keeps a map from `envelope_id` to the current entry. When a new result arrives, the reducer looks up the `envelope_id`. If the key already exists, the new result is merged into the existing entry in place, so the entry is updated rather than appended. If the key is new, the entry is added. The order of the returned list is the order in which each `envelope_id` was first seen, but that order is not meaningful. What matters is that each logical task appears exactly once, and later updates to it overwrite earlier ones.

This is what makes the merge correct without ordering. A plain [`operator.add`](https://docs.python.org/3/library/operator.html#operator.add) would append every result, so if a persist node later updates an existing entry, you get a duplicate. `envelope_reducer` recognizes the update as the same `envelope_id` and merges it in place, so there is no duplicate regardless of when the update arrives.

## Why not completion notifiers?

When you want parallel work, the natural instinct is to spawn it and wait for a completion notifier: a callback, a future, or an event that fires when the work is done. That model assumes the work can be preempted, like an OS thread that the scheduler can pause and resume.

LangGraph threads are not that. A LangGraph thread (the `thread_id`) is about state persistence and checkpointing, not OS-thread scheduling. A graph run is a sequence of steps, and you cannot pause a running node mid-execution and resume it elsewhere. So the spawn-and-notify model does not map onto LangGraph.

[`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send) is not a way to spawn OS threads or register completion callbacks. It lets a node dynamically dispatch multiple tasks. LangGraph schedules those tasks, runs each one to completion, and merges the results back into the shared state. (When several tasks are ready at once, LangGraph runs them together in one superstep.)

The key shift is this: instead of "spawn work and wait for a notifier", you express parallelism as "dispatch N independent tasks, then merge their results with a reducer". The reducer is a pure function on state. It does not need to know when a task finished or in what order. It just combines the existing state with the new results. That is why you do not need a completion notifier: the reducer is the completion handling, built into the graph structure.

This is the Map/Reduce pattern. [`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send) is the Map (the fan-out). The reducer is the Reduce (the merge). `envelope_reducer` is the Reduce half, and it is what makes the merge correct when results arrive in non-deterministic order.

## What topologies it supports

- [`Send`](https://docs.langchain.com/oss/python/langgraph/reference/types#langgraph.types.Send) fan-out, where parallel workers accumulate results into shared state.
- Multi-agent graphs where a persist node updates already-collected results.
- Any graph state field that accumulates a list of result envelopes keyed by `envelope_id`.


## Example

See [examples/state_reducers.py](https://github.com/stokomax/langshark-bites/blob/main/examples/state_reducers.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/state_reducers.py
```

## API reference

::: langshark_bites.state_reducers
