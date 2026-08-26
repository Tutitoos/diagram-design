"""What a tool is, and the list of them.

A tool is a name, a description, a JSON Schema for its arguments and a handler
that turns those arguments into text. The handler returns a string because
that is what an MCP result carries; a handler that wants to return structure
serializes it, so there is exactly one place that decides how structure
reaches a client.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

Handler = Callable[[Dict[str, Any]], str]


class Tool:
    __slots__ = ("name", "description", "schema", "handler")

    def __init__(self, name: str, description: str, schema: Dict[str, Any], handler: Handler):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler

    def wire(self) -> Dict[str, Any]:
        """The shape `tools/list` hands to a client."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
        }


def obj(properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    """A JSON Schema object, closed.

    `additionalProperties: false` on purpose: a caller that misspells an
    argument gets told so, instead of having it silently dropped and getting
    the default behaviour back with no sign that the argument was ignored.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


NO_ARGS = obj({}, [])
