"""``tabpilot`` command line: serve, diagnose, and run the Ubuntu stack."""

from __future__ import annotations

import argparse
import sys

from . import __version__, doctor, systemd
from .config import Config, add_common_args
from .errors import TabPilotError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tabpilot",
        description="Read and drive a real, logged-in Chrome over MCP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  tabpilot serve                    run the MCP server on stdio (the default)
  tabpilot doctor                   explain why the browser is unreachable
  tabpilot install-stack            install the Ubuntu 24/7 Chrome units
  tabpilot up                       start that stack
  tabpilot logs chrome -f           follow Chrome's log
""",
    )
    parser.add_argument("--version", action="version", version=f"tabpilot {__version__}")
    add_common_args(parser)

    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="run the MCP server on stdio (default)")
    add_common_args(serve)

    diagnose = subparsers.add_parser("doctor", help="check every prerequisite and say how to fix it")
    add_common_args(diagnose)

    tabs = subparsers.add_parser("tabs", help="list open tabs, to verify the connection by hand")
    add_common_args(tabs)
    tabs.add_argument("pattern", nargs="?", help="optional URL regex to filter by")

    install = subparsers.add_parser(
        "install-stack",
        help="install the systemd units for a 24/7 headless Chrome (Linux)",
    )
    add_common_args(install)
    install.add_argument("--screen", default="1920x1080x24", help="Xvfb screen geometry")
    install.add_argument("--start-url", default="about:blank", help="page Chrome opens on")
    install.add_argument("--extra-flags", default="", help="extra Chrome flags, space-separated")
    install.add_argument(
        "--vnc-insecure", action="store_true",
        help="run VNC with no password. Anyone who reaches the port controls your logged-in browser.",
    )
    install.add_argument("--no-enable", action="store_true", help="write the units but do not enable them")

    up = subparsers.add_parser("up", help="start the managed stack (Linux)")
    add_common_args(up)
    up.add_argument("--restart", action="store_true", help="restart instead of start")

    down = subparsers.add_parser("down", help="stop the managed stack (Linux)")
    add_common_args(down)

    status = subparsers.add_parser("status", help="show the managed stack's state (Linux)")
    add_common_args(status)

    logs = subparsers.add_parser("logs", help="show a unit's log (Linux)")
    logs.add_argument("unit", nargs="?", default="chrome",
                      help="xvfb, wm, chrome, or vnc (default: chrome)")
    logs.add_argument("-n", "--lines", type=int, default=50, help="how many lines")
    logs.add_argument("-f", "--follow", action="store_true", help="keep following")

    uninstall = subparsers.add_parser(
        "uninstall-stack", help="remove the systemd units, keeping the Chrome profile (Linux)"
    )
    add_common_args(uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.from_env().merge_args(args)
    command = args.command or "serve"

    try:
        if command == "serve":
            return _serve(config)

        if command == "doctor":
            report, code = doctor.run(config)
            print(report)
            return code

        if command == "tabs":
            from .session import Session
            from . import tabs as tabs_module

            session = Session(config)
            try:
                print(f"backend: {session.backend.describe()}\n")
                print(tabs_module.render_list(session.list_tabs(), getattr(args, "pattern", None)))
            finally:
                session.close()
            return 0

        if command == "install-stack":
            print(systemd.install(
                config,
                vnc_insecure=args.vnc_insecure,
                start_url=args.start_url,
                screen=args.screen,
                extra_flags=args.extra_flags,
                enable=not args.no_enable,
            ))
            return 0

        if command == "up":
            print(systemd.up(config, restart=args.restart))
            return 0

        if command == "down":
            print(systemd.down(config))
            return 0

        if command == "status":
            print(systemd.status(config))
            return 0

        if command == "logs":
            return systemd.logs(args.unit, args.lines, args.follow)

        if command == "uninstall-stack":
            print(systemd.uninstall())
            return 0

        parser.error(f"unknown command {command!r}")
        return 2

    except TabPilotError as exc:
        print(exc.to_text(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _serve(config: Config) -> int:
    """Run the MCP server on stdio.

    Nothing is printed to stdout: on stdio transport that stream *is* the
    protocol, and a stray line breaks the client's parser.
    """
    from .server import create_server

    server = create_server(config)
    try:
        server.run()
    finally:
        session = getattr(server, "_tabpilot_session", None)
        if session is not None:
            session.close()
    return 0


def main_serve(argv: list[str] | None = None) -> int:
    """Entry point for ``tabpilot-mcp``: serve, with no subcommand needed."""
    parser = build_parser()
    args = parser.parse_args(argv or [])
    return _serve(Config.from_env().merge_args(args))


if __name__ == "__main__":
    raise SystemExit(main())
