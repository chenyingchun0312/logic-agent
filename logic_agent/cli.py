from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .decoders import supported_protocols
from .errors import LogicAgentError
from .mcp_clients import SCOPES, Locations, format_clients, format_report, install, list_clients
from .mcp_server import main as mcp_main
from .service import LogicAgentService


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = _move_global_options(argv)
    parser = argparse.ArgumentParser(prog="logic-agent", description="Logic analyzer gateway for AI agents")
    parser.add_argument("--data-dir", type=Path, default=None, help="Capture archive (default: .logic_agent_data)")
    parser.add_argument("--backend", choices=["mock", "sigrok", "dslab"], default=None, help="Override backend for every command")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("mcp", help="Run the MCP stdio server")
    setup = sub.add_parser(
        "mcp-install",
        help="Add this MCP server to Codex, Claude Code, Copilot, Cursor, Kiro, Windsurf, Cline",
    )
    setup.add_argument("clients", nargs="*", metavar="CLIENT", help="Client ids (default: every detected agent); --list shows them")
    setup.add_argument("--list", action="store_true", help="Show the supported agents and where their config file lives")
    setup.add_argument("--all", action="store_true", help="Also configure agents that were not detected")
    setup.add_argument("--remove", action="store_true", help="Remove the server instead of adding it")
    setup.add_argument("--dry-run", action="store_true", help="Print copy-paste snippets without touching any file")
    setup.add_argument("--scope", choices=list(SCOPES), default="user", help="user: per-user config; project: config in --cwd")
    setup.add_argument("--json", action="store_true", help="Machine-readable result")
    setup.add_argument("--no-verify", action="store_true", help="Do not start the server to verify the launch spec")
    setup.add_argument("--home", type=Path, default=None, help="Override the user home directory (testing)")
    setup.add_argument("--cwd", type=Path, default=None, help="Project directory for --scope project")
    sub.add_parser("capabilities", help="Show backends and limits")
    sub.add_parser("devices", help="List connected devices")
    sub.add_parser("captures", help="List saved captures")
    sub.add_parser("self-test", help="Run the DSLogic internal hardware self-test")

    imp = sub.add_parser("import-csv", help="Import a DSView CSV export")
    imp.add_argument("path")
    imp.add_argument("--channels", help="Comma-separated channel columns")
    imp.add_argument("--label")

    cap = sub.add_parser("capture", help="Run a capture and print its summary")
    cap.add_argument("--channels", required=True, help="Comma-separated channel ids, e.g. D0,D1")
    cap.add_argument("--sample-rate", type=int, required=True)
    cap.add_argument("--duration", type=float, required=True)
    cap.add_argument("--device")
    cap.add_argument("--label")

    demux = sub.add_parser("uart", help="Decode UART from a saved capture")
    demux.add_argument("capture_id")
    demux.add_argument("channel")
    demux.add_argument("--baud", type=int, default=115_200)
    demux.add_argument("--start", type=float, default=0.0)
    demux.add_argument("--duration", type=float)
    demux.add_argument("--idle-low", action="store_true", help="Decode idle-low signalling (start bit is high)")

    decode = sub.add_parser("decode", help="Decode a saved capture with bundled single-layer or stacked protocol decoders")
    decode.add_argument("capture_id")
    decode.add_argument("protocol", choices=supported_protocols())
    decode.add_argument("--channels", required=True, help="Role mapping, e.g. scl=D0,sda=D1")
    decode.add_argument("--options", default=None, help="Comma-separated decoder options, e.g. cpol=0,cpha=0")
    decode.add_argument("--start", type=float, default=0.0)
    decode.add_argument("--duration", type=float)
    decode.add_argument("--max-events", type=int, default=500)
    decode.add_argument("--include-bits", action="store_true", help="Include per-bit annotations (large)")
    decode.add_argument(
        "--include-lower-layers",
        action="store_true",
        help="For stacked protocols, include lower-layer events such as SPI/UART",
    )

    period = sub.add_parser("period", help="Measure period/frequency/duty cycle")
    period.add_argument("capture_id")
    period.add_argument("channel")
    period.add_argument("--start", type=float, default=0.0)
    period.add_argument("--duration", type=float)

    view = sub.add_parser("view", help="Export a saved capture to VCD and open it in a waveform viewer")
    view.add_argument("capture_id")
    view.add_argument("--output", type=Path, help="VCD path (default: <capture dir>/capture.vcd)")
    view.add_argument("--viewer", help="Viewer executable (default: pulseview, then gtkwave)")
    view.add_argument("--no-open", action="store_true", help="Write the VCD but do not launch a viewer")

    demo = sub.add_parser("demo", help="Capture the mock UART signal and decode it")

    args = parser.parse_args(argv)
    if args.command == "mcp":
        mcp_args = ["--data-dir", str(args.data_dir)] if args.data_dir else []
        if args.backend:
            mcp_args += ["--backend", args.backend]
        return mcp_main(mcp_args)
    if args.command == "mcp-install":
        return _run_mcp_install(args)

    service = LogicAgentService(data_dir=args.data_dir)
    if args.backend:
        service.default_backend = args.backend

    try:
        if args.command == "capabilities":
            payload = service.capabilities()
        elif args.command == "devices":
            payload = service.list_devices()
        elif args.command == "captures":
            payload = service.list_captures()
        elif args.command == "self-test":
            payload = service.self_test()
        elif args.command == "import-csv":
            payload = service.import_csv(
                {
                    "path": args.path,
                    "channels": [item.strip() for item in args.channels.split(",") if item.strip()] if args.channels else None,
                    "label": args.label,
                }
            )
        elif args.command == "capture":
            payload = service.capture(
                {
                    "channels": [item.strip() for item in args.channels.split(",") if item.strip()],
                    "sample_rate_hz": args.sample_rate,
                    "duration_s": args.duration,
                    "device": args.device,
                    "label": args.label,
                }
            )
        elif args.command == "uart":
            payload = service.decode_uart(
                {
                    "capture_id": args.capture_id,
                    "channel": args.channel,
                    "start_s": args.start,
                    "duration_s": args.duration,
                    "uart": {"baud": args.baud, "idle_high": not args.idle_low},
                }
            )
        elif args.command == "decode":
            payload = service.decode_protocol(
                {
                    "capture_id": args.capture_id,
                    "protocol": args.protocol,
                    "channels": _parse_mapping(args.channels),
                    "options": _parse_options(args.options),
                    "start_s": args.start,
                    "duration_s": args.duration,
                    "max_events": args.max_events,
                    "include_bits": args.include_bits,
                    "include_lower_layers": args.include_lower_layers,
                }
            )
        elif args.command == "period":
            payload = service.measure_period(
                {
                    "capture_id": args.capture_id,
                    "channel": args.channel,
                    "start_s": args.start,
                    "duration_s": args.duration,
                }
            )
        elif args.command == "view":
            payload = _export_and_view(service, args.capture_id, args.output, args.viewer, args.no_open)
        elif args.command == "demo":
            demo_service = LogicAgentService(data_dir=args.data_dir)
            demo_service.default_backend = "mock"
            captured = demo_service.capture(
                {
                    "channels": ["D0"],
                    "sample_rate_hz": 1_000_000,
                    "duration_s": 0.02,
                    "label": "mock UART demo",
                }
            )
            decoded = demo_service.decode_uart(
                {"capture_id": captured["capture_id"], "channel": "D0", "uart": {"baud": 115_200}}
            )
            payload = {"capture": captured, "uart": decoded}
        else:  # pragma: no cover - argparse enforces choices
            parser.error(f"unknown command: {args.command}")
    except LogicAgentError as exc:
        print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=False))
        return 2

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _run_mcp_install(args: argparse.Namespace) -> int:
    """Write (or preview, or remove) the MCP entry in every supported agent's config."""
    locations = Locations.detect(home=args.home, cwd=args.cwd)
    try:
        if args.list:
            rows = list_clients(locations)
            print(json.dumps(rows, indent=2, ensure_ascii=False) if args.json else format_clients(rows))
            return 0
        installation = install(
            locations=locations,
            scope=args.scope,
            only=args.clients,
            include_all=args.all,
            remove=args.remove,
            dry_run=args.dry_run,
            verify=not args.no_verify,
            data_dir=args.data_dir,
            backend=args.backend,
        )
    except LogicAgentError as exc:
        print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(installation.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_report(installation, dry_run=args.dry_run))
    return 0 if installation.ok else 2


def _parse_mapping(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise LogicAgentError(f"invalid channel mapping: {item!r}; expected role=D0")
        key, channel = item.split("=", 1)
        key, channel = key.strip(), channel.strip()
        if not key or not channel:
            raise LogicAgentError(f"invalid channel mapping: {item!r}; expected role=D0")
        result[key] = channel
    if not result:
        raise LogicAgentError("--channels must contain at least one role=D0 mapping")
    return result


def _export_and_view(
    service: LogicAgentService,
    capture_id: str,
    output: Path | None,
    viewer: str | None,
    no_open: bool,
) -> dict[str, object]:
    """Write a standards-compliant VCD from archived channels and optionally launch a viewer."""
    manifest = service.store.manifest(capture_id)
    channel_ids = [str(item["id"]) for item in manifest["channels"]]
    if not channel_ids:
        raise LogicAgentError("capture has no channels")

    lines = [
        f"$date {manifest.get('created_at', '')} $end",
        "$version logic-agent view $end",
        f"$comment {manifest.get('device', {}).get('name', 'logic analyzer')}, {manifest.get('sample_rate_hz')} Hz $end",
        "$timescale 1ns $end",
        "$scope module logic $end",
    ]
    identifiers = [chr(ord("!") + index) for index in range(len(channel_ids))]
    for channel_id, identifier in zip(channel_ids, identifiers):
        lines.append(f"$var wire 1 {identifier} {channel_id} $end")
    lines += ["$upscope $end", "$enddefinitions $end", "#0"]

    sample_rate = float(manifest["sample_rate_hz"])
    samples_by_channel = {channel_id: service.store.load_channel(capture_id, channel_id)[0] for channel_id in channel_ids}
    sample_count = int(manifest["sample_count"])
    previous = {channel_id: None for channel_id in channel_ids}
    last_timestamp = None
    for index in range(sample_count):
        timestamp = round(index * 1_000_000_000 / sample_rate)
        for channel_id, identifier in zip(channel_ids, identifiers):
            value = samples_by_channel[channel_id][index]
            if previous[channel_id] == value:
                continue
            if timestamp != last_timestamp:
                lines.append(f"#{timestamp}")
                last_timestamp = timestamp
            lines.append(f"{value}{identifier}")
            previous[channel_id] = value

    target = output or (service.store.artifact_path(capture_id) / "capture.vcd")
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    launched = False
    viewer_path = None if no_open else _find_viewer(viewer)
    if viewer_path:
        launch_args = [viewer_path, "-D", "-c", str(target)] if _is_pulseview(viewer_path) else [viewer_path, str(target)]
        subprocess.Popen(launch_args, close_fds=True)
        launched = True
    return {
        "ok": True,
        "capture_id": capture_id,
        "vcd_path": str(target),
        "sample_rate_hz": int(sample_rate),
        "sample_count": sample_count,
        "channels": channel_ids,
        "viewer": viewer_path,
        "launched": launched,
    }


def _is_pulseview(path: str) -> bool:
    return Path(path).name.lower() == "pulseview.exe"


def _find_viewer(requested: str | None) -> str | None:
    import shutil

    if requested:
        resolved = shutil.which(requested) or requested
        if Path(resolved).is_file():
            return str(Path(resolved).resolve())
        raise LogicAgentError(f"viewer not found: {requested}")
    candidates = [
        os.environ.get("PULSEVIEW_EXE"),
        r"C:\Program Files (x86)\sigrok\PulseView\pulseview.exe",
        r"C:\Program Files\sigrok\PulseView\pulseview.exe",
        shutil.which("pulseview"),
        shutil.which("gtkwave"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return None


def _parse_options(value: str | None) -> dict[str, object]:
    result: dict[str, object] = {}
    if not value:
        return result
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise LogicAgentError(f"invalid decoder option: {item!r}; expected key=value")
        key, option = item.split("=", 1)
        key, option = key.strip(), option.strip()
        if not key or not option:
            raise LogicAgentError(f"invalid decoder option: {item!r}; expected key=value")
        # Preserve booleans and numeric options as typed values when possible.
        # The decoder registry performs range, enum, and integer/floating-point
        # validation; non-numeric values remain strings for enum options.
        lowered = option.lower()
        if lowered in {"true", "false"}:
            result[key] = lowered == "true"
            continue
        try:
            result[key] = int(option)
        except ValueError:
            try:
                result[key] = float(option)
            except ValueError:
                result[key] = option
    return result


def _move_global_options(argv: list[str]) -> list[str]:
    """Allow --data-dir/--backend on either side of the subcommand.

    argparse only accepts global options before the subcommand; users and MCP
    launch snippets routinely append them. Move only exact, value-taking flags.
    """
    moved: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in {"--data-dir", "--backend"} and index + 1 < len(argv):
            moved.extend([item, argv[index + 1]])
            index += 2
            continue
        rest.append(item)
        index += 1
    return moved + rest


if __name__ == "__main__":
    sys.exit(main())




