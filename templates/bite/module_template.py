"""<BITE_NAME>: <one-line purpose>.

Why this exists
---------------
<2-4 sentences on the problem this bite solves and for whom. Motivate the
need — do not restate the API.>

Usage
-----
    from langshark_bites.<bite_name> import <public_func>

    result = <public_func>(...)
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def <public_func>(value: str) -> str:
    """<Short description of what the function does>.

    Args:
        value: <what the argument is>.

    Returns:
        <what the return value is>.

    Raises:
        ValueError: <when this is raised>.
    """
    # ...implementation...
    return value