"""
CLI entry point: `tgmock serve --port 8999`

Starts the fake Telegram API server standalone (without a bot).
Useful for debugging, manual testing, or as a backend for the MCP server.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from tgmock.server import TelegramMockServer


async def _serve(port: int, token: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mock = TelegramMockServer(token=token, port=port)
    runner = await mock.start()
    print(f"tgmock server running on http://localhost:{port}")
    print(f"Token: {token}")
    print("Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tgmock",
        description="Fake Telegram Bot API server for testing",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the mock server")
    serve_parser.add_argument("--port", type=int, default=8999, help="Port to listen on (default: 8999)")
    serve_parser.add_argument("--token", default="test:token", help="Fake bot token (default: test:token)")

    mcp_parser = subparsers.add_parser("mcp", help="Start the MCP server (requires tgmock[mcp])")

    args = parser.parse_args()

    if args.command == "serve":
        asyncio.run(_serve(args.port, args.token))
    elif args.command == "mcp":
        from tgmock.mcp_server import main as mcp_main
        asyncio.run(mcp_main())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
