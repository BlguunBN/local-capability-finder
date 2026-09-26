"""Minimal stdio MCP server for the local capability finder."""

from __future__ import annotations

import json
import sys
from typing import Any

_PROTOCOL_VERSION = "2025-11-25"
_SUPPORTED_HANDSHAKE_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "search_capabilities",
            "description": "Search local skills, tools, and plugins. Returns up to three concise matches by default.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language capability need."},
                    "kind": {"type": "string", "description": "Optional type filter, such as skill, tool, or plugin."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 3},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_capability",
            "description": "Get full details for one exact capability ID returned by search_capabilities.",
            "inputSchema": {
                "type": "object",
                "properties": {"capability_id": {"type": "string"}},
                "required": ["capability_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "activate_skill",
            "description": "Activate a skill by its exact ID for a named agent. Does not install capabilities.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "capability_id": {"type": "string", "description": "Exact skill ID."},
                    "agent": {"type": "string", "description": "Target agent name."},
                },
                "required": ["capability_id", "agent"],
                "additionalProperties": False,
            },
        },
    ]


def _call_tool(name: str, arguments: Any) -> Any:
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")

    # Import on demand so protocol discovery can still work while the finder is
    # being installed or if a deployment has not yet configured its data files.
    import capability_finder

    if name == "search_capabilities":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        kind = arguments.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise ValueError("kind must be a string")
        limit = arguments.get("limit", 3)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be an integer from 1 to 20")
        return capability_finder.search(query.strip(), kind=kind, limit=limit)

    if name == "get_capability":
        capability_id = arguments.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id:
            raise ValueError("capability_id must be a non-empty string")
        result = capability_finder.show(capability_id)
        if result is None:
            raise ValueError(f"Unknown capability ID: {capability_id}")
        return result

    if name == "activate_skill":
        capability_id = arguments.get("capability_id")
        agent = arguments.get("agent")
        if not isinstance(capability_id, str) or not capability_id:
            raise ValueError("capability_id must be a non-empty string")
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent must be a non-empty string")
        result = capability_finder.activate(capability_id, agent.strip())
        if not isinstance(result, dict):
            raise RuntimeError("Capability finder returned an invalid activation result")
        return result

    raise LookupError(f"Unknown tool: {name}")


def _response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def _encode(response: dict[str, Any]) -> str:
    try:
        return json.dumps(response, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        return json.dumps(
            _response(None, error={"code": -32603, "message": str(exc)}),
            ensure_ascii=True,
            separators=(",", ":"),
        )


def _dispatch(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _response(None, error={"code": -32600, "message": "Invalid Request"})
    method = message.get("method")
    request_id = message.get("id")
    # Notifications have no response, including unknown notifications.
    if "id" not in message:
        return None
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return _response(request_id, error={"code": -32600, "message": "Invalid Request"})

    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = requested if isinstance(requested, str) and requested in _SUPPORTED_HANDSHAKE_VERSIONS else _PROTOCOL_VERSION
        return _response(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "local-capability-finder", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _response(request_id, error={"code": -32602, "message": "Tool name is required"})
        try:
            result = _call_tool(name, params.get("arguments", {}))
            return _response(
                request_id,
                {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
            )
        except LookupError as exc:
            return _response(request_id, error={"code": -32602, "message": str(exc)})
        except ValueError as exc:
            return _response(request_id, error={"code": -32602, "message": str(exc)})
        except Exception as exc:  # surface backend failures as tool errors, not transport crashes
            return _response(
                request_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
    return _response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    """Read one JSON-RPC message per stdin line and write responses to stdout."""
    # Windows pipes can inherit a legacy code page. MCP messages use UTF-8.
    reconfigure = getattr(sys.stdin, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = _dispatch(message)
        except json.JSONDecodeError:
            response = _response(None, error={"code": -32700, "message": "Parse error"})
        except Exception as exc:
            # Keep stdout protocol-clean while making startup/backend failures visible.
            response = _response(None, error={"code": -32603, "message": str(exc)})
        if response is not None:
            sys.stdout.write(_encode(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
