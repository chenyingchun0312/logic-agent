from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, BinaryIO

from . import __version__
from .decoders import protocol_specs, supported_protocols
from .errors import LogicAgentError
from .service import DEFAULT_RESOURCE_URI_TEMPLATE, LogicAgentService


TOOLS = [
    {
        "name": "capabilities",
        "description": "Report available backends, safety limits, and the recommended workflow.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_devices",
        "description": "Discover connected logic analyzers without starting a capture.",
        "inputSchema": {
            "type": "object",
            "properties": {"backend": {"type": "string", "enum": ["mock", "sigrok", "dslab"]}},
            "additionalProperties": False,
        },
    },
    {
        "name": "capture",
        "description": (
            "Capture digital channels and persist them under a capture_id. Returns per-channel statistics "
            "(edges, pulse widths, rough frequency), never the raw sample array. Prefer a short window first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "channels": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "sample_rate_hz": {"type": "integer", "minimum": 1},
                "duration_s": {"type": "number", "exclusiveMinimum": 0},
                "backend": {"type": "string", "enum": ["mock", "sigrok", "dslab"]},
                "device": {"type": "string"},
                "label": {"type": "string", "maxLength": 120},
                "trigger": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string"},
                        "edge": {"type": "string", "enum": ["rising", "falling", "either"]},
                    },
                    "required": ["channel"],
                    "additionalProperties": False,
                },
            },
            "required": ["channels", "sample_rate_hz", "duration_s"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_captures",
        "description": "List saved captures, newest first.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "self_test",
        "description": (
            "Hardware self-test using the DSLogic internal pattern generator. "
            "Run this before protocol work to distinguish a broken capture path from a bad probe signal. "
            "It does not prove that external wiring is correct."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "analyze_window",
        "description": (
            "Analyze a bounded time window of a saved channel. Starts with summaries and at most max_edges "
            "transitions so the agent can drill down incrementally."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capture_id": {"type": "string"},
                "channel": {"type": "string"},
                "start_s": {"type": "number", "minimum": 0},
                "duration_s": {"type": "number", "exclusiveMinimum": 0},
                "max_edges": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "required": ["capture_id", "channel"],
            "additionalProperties": False,
        },
    },
    {
        "name": "decode_uart",
        "description": (
            "Decode UART/RS-232-style asynchronous serial from a saved channel window. "
            "Always report framing/parity warnings rather than silently trusting text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capture_id": {"type": "string"},
                "channel": {"type": "string"},
                "start_s": {"type": "number", "minimum": 0},
                "duration_s": {"type": "number", "exclusiveMinimum": 0},
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": 65536},
                "uart": {
                    "type": "object",
                    "properties": {
                        "baud": {"type": "integer", "minimum": 50},
                        "data_bits": {"type": "integer", "enum": [5, 6, 7, 8]},
                        "parity": {"type": "string", "enum": ["none", "even", "odd"]},
                        "stop_bits": {"type": "number", "enum": [1, 1.5, 2]},
                        "idle_high": {"type": "boolean"},
                        "inverted": {"type": "boolean"},
                        "msb_first": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["capture_id", "channel"],
            "additionalProperties": False,
        },
    },
    {
        "name": "decode_protocol",
        "description": (
            "Decode a saved capture with bundled single-layer or stacked protocol decoders. "
            f"Protocols: {', '.join(supported_protocols())}. "
            "Map protocol roles explicitly, for example channels={scl:D0,sda:D1}. "
            "Inspect capabilities for each protocol's required/optional channels and typed options. "
            "Returns bounded structured events, never raw waveform arrays."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capture_id": {"type": "string"},
                "protocol": {"type": "string", "enum": supported_protocols()},
                "channels": {
                    "type": "object",
                    "description": "Protocol role to capture channel, e.g. {\"scl\":\"D0\",\"sda\":\"D1\"}",
                    "additionalProperties": {"type": "string"},
                },
                "options": {
                    "type": "object",
                    "description": (
                        "Typed decoder options. Use decoder.option when stacked layers share "
                        "a name, for example spi.cpol=0. See capabilities."
                    ),
                    "additionalProperties": True,
                },
                "start_s": {"type": "number", "minimum": 0},
                "duration_s": {"type": "number", "exclusiveMinimum": 0},
                "max_events": {"type": "integer", "minimum": 1, "maximum": 5000},
                "include_bits": {"type": "boolean"},
                "include_lower_layers": {
                    "type": "boolean",
                    "description": "Include lower-layer annotations for stacked protocols.",
                },
            },
            "required": ["capture_id", "protocol", "channels"],
            "additionalProperties": False,
        },
    },
    {
        "name": "import_csv",
        "description": (
            "Import a DSView CSV export (Export Data -> CSV) as a capture. This is the reliable "
            "offline path when DSView was driven manually. Export a narrow time range first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "channels": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "label": {"type": "string", "maxLength": 120},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "measure_period",
        "description": (
            "Measure period, frequency, duty cycle and jitter of a clock/PWM channel. "
            "Returns periodic=false when fewer than two rising edges exist in the window."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capture_id": {"type": "string"},
                "channel": {"type": "string"},
                "start_s": {"type": "number", "minimum": 0},
                "duration_s": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["capture_id", "channel"],
            "additionalProperties": False,
        },
    },
]


class McpServer:
    def __init__(self, service: LogicAgentService, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> None:
        self.service = service
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer

    def serve(self) -> None:
        for raw in self.stdin:
            if not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                self._write(None, {"code": -32700, "message": "Parse error"})
                continue
            if not isinstance(message, dict) or "method" not in message:
                self._write(None, {"code": -32600, "message": "Invalid Request"})
                continue
            if "id" not in message:
                continue  # notifications do not receive responses
            self._write(message["id"], self._dispatch(message["method"], message.get("params") or {}))

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if method == "initialize":
                return self._result(
                    {
                        "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                        "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": {"name": "logic-agent", "version": __version__},
                        "instructions": (
                            "Capture a short window first. Inspect channel summaries and hints, then "
                            "call analyze_window on a narrower window. Use decode_protocol for "
                            "registered single-layer and stacked protocols, and decode_uart for "
                            "asynchronous serial. Treat "
                            "zero-edge or framing-error results as uncertainty, not proof of "
                            "protocol behavior. Never present raw waveforms to the user as analysis."
                        ),
                    }
                )
            if method == "ping":
                return self._result({})
            if method == "tools/list":
                return self._result({"tools": TOOLS})
            if method == "resources/list":
                return self._result(
                    {
                        "resources": [
                            {
                                "uri": DEFAULT_RESOURCE_URI_TEMPLATE.format(capture_id=item["id"]),
                                "name": f"capture {item['id']}",
                                "mimeType": "application/json",
                            }
                            for item in self.service.store.list()
                        ]
                    }
                )
            if method == "resources/read":
                return self._read_resource(params)
            if method == "tools/call":
                return self._call_tool(params)
            return self._error(-32601, f"Method not found: {method}")
        except LogicAgentError as exc:
            return self._error(-32000, exc.message, data=exc.to_dict())
        except Exception as exc:  # pragma: no cover - defensive protocol boundary
            return self._error(-32603, f"Internal error: {type(exc).__name__}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return self._error(-32602, "arguments must be an object")
        handlers = {
            "capabilities": lambda: self.service.capabilities(),
            "list_devices": lambda: self.service.list_devices(args.get("backend")),
            "capture": lambda: self.service.capture(args),
            "list_captures": self.service.list_captures,
            "self_test": self.service.self_test,
            "analyze_window": lambda: self.service.analyze_window(args),
            "decode_uart": lambda: self.service.decode_uart(args),
            "decode_protocol": lambda: self.service.decode_protocol(args),
            "import_csv": lambda: self.service.import_csv(args),
            "measure_period": lambda: self.service.measure_period(args),
        }
        try:
            payload = handlers[name]()
        except KeyError:
            return self._error(-32602, f"Unknown tool: {name}")
        except LogicAgentError as exc:
            payload = exc.to_dict()
        text = json.dumps(payload, ensure_ascii=False)
        is_error = payload.get("ok") is False
        if len(text) > self.service.limits.max_tool_chars:
            return self._error(-32001, "Tool result exceeded max_tool_chars; ask for a narrower window")
        return self._result(
            {
                "content": [{"type": "text", "text": text}],
                "structuredContent": payload,
                "isError": is_error,
            }
        )

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = str(params.get("uri", ""))
        prefix = "logic-agent://captures/"
        if not uri.startswith(prefix):
            return self._error(-32002, f"Unsupported resource URI: {uri}")
        capture_id = uri[len(prefix):].strip("/")
        text, mime = self.service.read_resource(capture_id)
        return self._result({"contents": [{"uri": uri, "mimeType": mime, "text": text}]})

    def _result(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "result": result}

    def _error(self, code: int, message: str, *, data: dict | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {"jsonrpc": "2.0", "error": error}

    def _write(self, request_id: Any, payload: dict[str, Any]) -> None:
        message = {"jsonrpc": "2.0", **payload}
        if request_id is not None:
            message["id"] = request_id
        self.stdout.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
        self.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="logic-agent-mcp", description="Logic analyzer MCP server (stdio)")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--backend", choices=["mock", "sigrok", "dslab"], default=None)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    service = LogicAgentService(data_dir=args.data_dir)
    if args.backend:
        service.default_backend = args.backend
    McpServer(service).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

