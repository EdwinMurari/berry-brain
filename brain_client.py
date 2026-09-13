#!/usr/bin/env python3
"""Stdio MCP adapter for the shared engine, through a server or local storage."""

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


MAX_BYTES = 512 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, config):
        self.base = config["url"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
            raise ValueError("brain URL must use HTTPS or loopback HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid brain URL")
        self.identity = config["identity"]
        token_file = Path(config["token_file"]).expanduser()
        mode = token_file.lstat()
        if not stat.S_ISREG(mode.st_mode) or (os.name != "nt" and mode.st_mode & 0o077):
            raise ValueError("brain token must be a private regular file")
        self.token = token_file.read_text().strip()
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, data=None):
        request = urllib.request.Request(self.base + "/v1/brain/" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + self.token, "X-Berry-App": self.identity,
                     "Content-Type": "application/json"})
        try:
            with self.http.open(request, timeout=25) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise RuntimeError("brain result exceeded limit")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Never echo credentials or unbounded response bodies.
            detail = exc.read(4096).decode(errors="replace")
            raise RuntimeError(f"brain HTTP {exc.code}: {detail}") from None
        except (TimeoutError, urllib.error.URLError) as exc:
            raise RuntimeError("brain unavailable; mutation status may be unknown. Retry identical arguments.") from exc


def dispatch(client, message):
    method = message.get("method")
    if method == "initialize":
        version = message.get("params", {}).get("protocolVersion")
        return {"protocolVersion": version if version in {"2024-11-05", "2025-03-26", "2025-06-18"} else "2025-06-18",
                "capabilities": {"tools": {}}, "serverInfo": {"name": "berry-brain", "version": "1.0.0"},
                "instructions": client.request("tools")["instructions"]}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": client.request("tools")["tools"]}
    if method == "tools/call":
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("tool parameters must be an object")
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        if not isinstance(name, str) or not name.startswith("brain_") or not name[6:].isascii() or not name[6:].isalpha():
            raise ValueError("unknown brain tool")
        try:
            # The shared engine validates action names and current access rights.
            result = client.request(name.removeprefix("brain_"), arguments)
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, separators=(",", ":"))}], "isError": False}
        except RuntimeError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    raise ValueError("method not found")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", type=Path, help="private HTTP client config")
    mode.add_argument("--local", action="store_true", help="use a private database on this machine")
    parser.add_argument("--data-dir", type=Path, help="local data directory; defaults to the user's application data")
    parser.add_argument("--identity", default="local", help="local client name for attribution")
    parser.add_argument("--project", action="append", help="allowed local project; repeat for more projects")
    parser.add_argument("--call", choices=["tools", "recall", "record", "propose", "trial", "feedback", "checkpoint", "history", "revise"])
    args = parser.parse_args()
    if args.local:
        from brain_local import LocalClient, default_directory
        client = LocalClient(args.data_dir or default_directory(), args.identity, args.project or ["default"])
    else:
        if args.data_dir or args.project or args.identity != "local":
            parser.error("--data-dir, --identity and --project require --local")
        client = Client(json.loads(args.config.expanduser().read_text()))
    if args.call:
        print(json.dumps(client.request(args.call, None if args.call == "tools" else json.load(sys.stdin)), ensure_ascii=False))
        return
    while True:
        line = sys.stdin.buffer.readline(MAX_BYTES + 1)
        if not line:
            break
        message = None
        try:
            if len(line) > MAX_BYTES:
                # Discard the rest of this frame. Its tail is not a new request.
                while line and not line.endswith(b"\n"):
                    line = sys.stdin.buffer.readline(MAX_BYTES + 1)
                raise ValueError("request exceeded limit")
            message = json.loads(line)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("invalid request")
            if "id" not in message:
                continue
            result = dispatch(client, message)
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                     "error": {"code": -32603, "message": str(exc)}}
        print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
