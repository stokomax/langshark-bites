"""Runnable example for the <bite_name> bite.

Shows how to use <public_func> in a realistic flow. Intended as the
end-to-end verification for this bite (see the repo's done-criteria).

Run with:

    uv run python examples/<bite_name>.py

See the docs: https://stokomax.github.io/langshark-bites/<bite_name>/
"""

from __future__ import annotations

import asyncio

from langshark_bites.<bite_name> import <public_func>


def simulate() -> str:
    """Realistic call using the bite's public API."""
    return <public_func>("... realistic input ...")


async def main() -> None:
    result = simulate()
    print(f"final result: {result}")


if __name__ == "__main__":
    asyncio.run(main())