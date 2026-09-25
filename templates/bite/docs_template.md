# <bite_name>

<One-line summary of what the bite provides>.

## The problem

<The pain this bite addresses, in a paragraph. Why the naive approach is
insufficient, who hits it, what it costs.>

## How this bite helps

<What the bite provides and the shape of its public API. Show the main
usage inline:>

```python
from langshark_bites.<bite_name> import <public_func>

result = <public_func>(...)
```

<Explain the core behavior and any configurable knobs.>

## What topologies it supports

- <Use case 1>
- <Use case 2>
- <Composition with other bites, if any>

## Example

See [examples/<bite_name>.py](https://github.com/stokomax/langshark-bites/blob/main/examples/<bite_name>.py) for a runnable example of this bite. Run it with:

```bash
uv run python examples/<bite_name>.py
```

## API reference

::: langshark_bites.<bite_name>