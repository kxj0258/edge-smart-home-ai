"""Project-specific, equivalent format projection for llama.cpp's schema subset.

The canonical schema and prompt keep their if/then/else relation. The pinned
llama.cpp b11351 grammar converter does not support conditionals, so the API
format receives two disjoint complete object branches. This is not a generic
JSON Schema compiler, validator, or output repair mechanism.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .contract import AGENT_RESPONSE_KINDS


FORMAT_ADAPTER = "agent-response-conditional-to-disjoint-oneOf-v1"
_RELATION = [{
    "if": {"properties": {"kind": {"const": "plan"}}, "required": ["kind"]},
    "then": {"properties": {"commands": {"minItems": 1}}},
    "else": {"properties": {"commands": {"maxItems": 0}}},
}]
_ANNOTATIONS = ("$schema", "$id", "title", "description")


def ollama_agent_response_format(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Project only the known project conditional, preserving other constraints.

    For the shared object constraints B, the original relation is
    B AND ((kind=plan AND n>=1) OR (kind!=plan AND n=0)). The kind enum covers
    exactly the existing five kinds. The returned branches use that partition
    and are disjoint, making oneOf equivalent to the backend's union/anyOf.
    Unknown conditional shapes fail instead of being silently weakened.
    """
    if not isinstance(schema, Mapping):
        raise ValueError("Agent format projection requires a schema object")
    value = deepcopy(dict(schema))
    if value.get("allOf") != _RELATION or value.get("type") != "object":
        raise ValueError("Agent format projection requires the known kind/commands conditional")
    properties = value.get("properties", {})
    kind = properties.get("kind", {})
    kinds = kind.get("enum")
    if (kind.get("type") != "string" or set(kind) != {"type", "enum"}
            or not isinstance(kinds, list) or any(not isinstance(k, str) for k in kinds)
            or len(kinds) != len(AGENT_RESPONSE_KINDS)
            or set(kinds) != AGENT_RESPONSE_KINDS):
        raise ValueError("Agent format projection requires the existing five-kind enum")
    if set(value.get("required", [])) != {"kind", "message", "commands"}:
        raise ValueError("Agent format projection requires existing required wire fields")
    commands = properties.get("commands", {})
    if commands.get("type") != "array":
        raise ValueError("Agent format projection requires commands array")
    for name, default in (("minItems", 0), ("maxItems", 8)):
        count = commands.get(name, default)
        if type(count) is not int or count < 0:
            raise ValueError("Agent format projection requires nonnegative item bounds")

    # Do not mix root properties and oneOf: the pinned backend's parser visits
    # alternatives before properties. Each alternative must carry the full B.
    annotations = {key: value[key] for key in _ANNOTATIONS if key in value}
    value.pop("allOf")
    for key in annotations:
        value.pop(key)
    plan = deepcopy(value)
    plan["properties"]["kind"]["const"] = "plan"
    plan["properties"]["commands"]["minItems"] = max(1, commands.get("minItems", 0))
    nonplan = deepcopy(value)
    nonplan["properties"]["kind"]["enum"] = [k for k in kinds if k != "plan"]
    nonplan["properties"]["commands"]["maxItems"] = 0
    return {**annotations, "oneOf": [plan, nonplan]}
