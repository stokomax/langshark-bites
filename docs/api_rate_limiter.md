# api_rate_limiter

Distributed rate limiting for external API calls made by LangGraph subagents.

## The problem

Your agents call external APIs. When you scale out, you run into a limit that a single-process limiter cannot handle.

A per-process semaphore gives each process its own budget. If you run three replicas, your effective rate limit becomes the configured limit times three. You exceed the provider's cap and get throttled, even though each process thinks it is behaving.

The same thing happens with `Send` fan-out. When one superstep dispatches many parallel subagents, they all call the same API at once. A per-process limit does not coordinate them.

## How this bite helps

It keeps the token bucket state in Redis, shared across every process, and updates it atomically with a Lua script. Callers across different workers never race on read-modify-write. If Redis is unreachable, it falls back to an in-process semaphore so the system stays up.

## Why Redis, and how it is used

The core problem is that a rate limit is a shared resource. Your agents run in many processes at once, and they all call the same external API. Each process needs to know how much of the shared budget is left, and they all need to agree on it. A per-process counter cannot do that, because each process only sees its own count.

Redis is the shared, single source of truth for that budget. Here is how it is used:

- **The bucket lives in Redis, not in your process.** For each provider, Redis stores a small record with two numbers: how many tokens are left in the bucket, and when it was last refilled. Every process reads and writes the same record, so they all share one budget.
- **Updates are atomic.** When a process wants a token, it runs a small Lua script on the Redis server. The script checks the bucket, refills it based on elapsed time, and decrements it if a token is available, all in one atomic step. Because the check-and-update happens inside Redis, two processes can never both read the same token and both think they got it. This is what prevents the race that a plain read-then-write would have.
- **The token bucket algorithm matches how providers describe their limits.** Most external APIs phrase their limit as "N requests per minute with a burst up to N". That is exactly token-bucket semantics: a capacity (the burst) and a refill rate (tokens per second). The Lua script implements this so the limiter behaves the way the provider actually enforces its own limit.

In a LangGraph context, this matters because a single graph can dispatch many parallel subagents in one superstep (via `Send`), and those subagents may run in different processes. Without a shared budget, each subagent would think it has the full limit to itself. Redis is what makes the budget shared and the coordination correct.

The sequence below shows what happens when a process asks for a token. The key point is that the check-and-update happens inside Redis in one atomic step, so two processes can never both get the same token.

```mermaid
sequenceDiagram
    participant P1 as Process 1
    participant P2 as Process 2
    participant R as Redis (shared bucket)

    P1->>R: run Lua script (check + refill + decrement)
    P2->>R: run Lua script (check + refill + decrement)
    Note over R: Redis serializes the two scripts
    R-->>P1: allowed = 1 (token granted)
    R-->>P2: allowed = 0 (bucket empty, wait)
```

If Redis is unreachable, the limiter falls back to an in-process token bucket. Rate limiting still works per process, so the system stays up, but there is no cross-replica coordination until Redis is available again. The flowchart below shows this decision path.

```mermaid
flowchart TD
    A[Process wants a token] --> B{Redis reachable?}
    B -- yes --> C[Run Lua script on Redis]
    C --> D{Token available?}
    D -- yes --> E[Grant token]
    D -- no --> F[Wait, then retry]
    B -- no --> G[Use in-process token bucket]
    G --> H{Token available?}
    H -- yes --> E
    H -- no --> F
```

## Developing and testing without Redis

You do not need a Redis server to develop or test a LangGraph app that uses this bite. The limiter is designed so the fallback is automatic.

- **Locally, without Redis.** If you construct a `RateLimiter` and no Redis is reachable, the first acquire attempt tries to connect, fails, and the limiter switches to the in-process token bucket. Your graph runs and rate limiting still works within the single process. This is the common local-development case: you can build and test your graph without standing up Redis.
- **In tests.** The same fallback applies. A test that exercises a rate-limited node does not need Redis. The in-process bucket grants tokens immediately when the bucket is full, so tests behave deterministically for the common case.
- **When you want to verify the distributed behavior.** To test the cross-replica coordination, run a Redis server (for example, `docker run -p 6379:6379 redis`) and point the limiter at it via `REDIS_URL`. The limiter will use the shared Redis bucket instead of the local fallback.

The practical takeaway: develop and test against the in-process fallback, and only bring up Redis when you want to verify or run the distributed, multi-replica behavior.

## What topologies it supports

- Multi-replica Agent Server deployments, where several processes call the same external API.
- `Send` fan-out, where many parallel subagents in one superstep call the same API.
- Any node that calls an external API and needs a shared budget.


## Example

See [examples/rate_limiter.py](https://github.com/stokomax/langshark-bites/blob/main/examples/rate_limiter.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/rate_limiter.py
```

## API reference

::: langshark_bites.api_rate_limiter
