# state_reducers

LangGraph state reducers for multi-agent accumulation.

## The problem

When `Send` fan-out dispatches parallel workers, their results merge back into the shared graph state in non-deterministic order. A plain `operator.add` duplicates rows whenever a later node updates an existing entry. For example, a persist node flips `persisted=True` on an already-collected result, and now that result appears twice.

## What is `Send`?

`Send` is a LangGraph function that lets a node dynamically dispatch multiple tasks in a single step. It is the way you express "run this same work for each of these inputs, in parallel".

Here is how `Send` works. A node returns a list of `Send` objects, each carrying a target node (or subgraph) and an input for that target. LangGraph then schedules one task per `Send`, runs each task to completion, and merges the results back into the shared state.

```python
from langgraph.types import Send

def fan_out(state):
    # One task per ticker, each running the "analyze" node.
    return [Send("analyze", {"ticker": t}) for t in state["tickers"]]
```

The key idea is that `Send` is about dispatching work, not about waiting for it. You do not get a handle to wait on or a callback to register. You hand the tasks to LangGraph, and LangGraph handles the scheduling and the merge. That is why the merge step, the reducer, matters so much: it is the part that collects the results, and it has to work correctly no matter what order the tasks finish in.

## How this bite helps

`envelope_reducer` upserts result entries by a stable key (`task_id`, falling back to `worker:as_of`). Updates merge in place instead of appending duplicates.

## Where does `task_id` come from?

`task_id` is not something the reducer invents. It is a field on each result envelope that your workers produce. When a worker finishes, it returns a dict that includes a `task_id` identifying which logical task that result belongs to. In the market-analysis scenario, each ticker is a task, so each worker returns a result with `task_id` set to its ticker.

The reducer does not care how you assign `task_id`. It only requires that the same logical task always uses the same `task_id`, so that updates to that task can be recognized as the same entry. If a worker does not set `task_id`, the reducer falls back to a composite key of `worker:as_of`, which is a stable identifier for a worker's output at a point in time.

## How `task_id` solves the ordering problem

The core difficulty is that parallel results arrive in non-deterministic order. You cannot rely on "the last result in the list is the newest". `task_id` gives you a way to identify which logical task a result belongs to, independent of its position in the list.

Here is how the reducer uses it. It keeps a map from `task_id` to the current entry. When a new result arrives, the reducer looks up the `task_id`. If the key already exists, the new result is merged into the existing entry in place, so the entry is updated rather than appended. If the key is new, the entry is added. The order of the returned list is the order in which each `task_id` was first seen, but that order is not meaningful. What matters is that each logical task appears exactly once, and later updates to it overwrite earlier ones.

This is what makes the merge correct without ordering. A plain `operator.add` would append every result, so if a persist node later updates an existing entry, you get a duplicate. `envelope_reducer` recognizes the update as the same `task_id` and merges it in place, so there is no duplicate regardless of when the update arrives.

## Why not completion notifiers?

When you want parallel work, the natural instinct is to spawn it and wait for a completion notifier: a callback, a future, or an event that fires when the work is done. That model assumes the work can be preempted, like an OS thread that the scheduler can pause and resume.

LangGraph threads are not that. A LangGraph thread (the `thread_id`) is about state persistence and checkpointing, not OS-thread scheduling. A graph run is a sequence of steps, and you cannot pause a running node mid-execution and resume it elsewhere. So the spawn-and-notify model does not map onto LangGraph.

`Send` is not a way to spawn OS threads or register completion callbacks. It lets a node dynamically dispatch multiple tasks. LangGraph schedules those tasks, runs each one to completion, and merges the results back into the shared state. (When several tasks are ready at once, LangGraph runs them together in one superstep.)

The key shift is this: instead of "spawn work and wait for a notifier", you express parallelism as "dispatch N independent tasks, then merge their results with a reducer". The reducer is a pure function on state. It does not need to know when a task finished or in what order. It just combines the existing state with the new results. That is why you do not need a completion notifier: the reducer is the completion handling, built into the graph structure.

This is the Map/Reduce pattern. `Send` is the Map (the fan-out). The reducer is the Reduce (the merge). `envelope_reducer` is the Reduce half, and it is what makes the merge correct when results arrive in non-deterministic order.

## What topologies it supports

- `Send` fan-out, where parallel workers accumulate results into shared state.
- Multi-agent graphs where a persist node updates already-collected results.
- Any graph state field that accumulates a list of result envelopes keyed by `task_id`.


## Example

See [examples/state_reducers.py](https://github.com/stokomax/langshark-bites/blob/main/examples/state_reducers.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/state_reducers.py
```

## API reference

::: langshark_bites.state_reducers
