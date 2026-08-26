"""The whole tool surface, in one list.

Order matters only for how a client reads `tools/list`, so it runs from
"what can this do" through the knowledge an agent needs to author, to the
deterministic operations on a file that already exists.
"""

from __future__ import annotations

from typing import Dict, List

from .registry import Tool
from . import deterministic, doctor, export, knowledge

TOOLS: List[Tool] = doctor.TOOLS + knowledge.TOOLS + deterministic.TOOLS + export.TOOLS

BY_NAME: Dict[str, Tool] = {tool.name: tool for tool in TOOLS}

__all__ = ["TOOLS", "BY_NAME", "Tool"]
