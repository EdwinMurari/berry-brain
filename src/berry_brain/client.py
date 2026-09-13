#!/usr/bin/env python3
"""Stdio MCP adapter for the shared engine, through a server or local storage."""

import argparse
from importlib.metadata import version as package_version
import http.client
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


MAX_BYTES = 512 * 1024


class ProtocolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, config):
        if not isinstance(config, dict) or any(not isinstance(config.get(key), str) or not config[key]
                                               for key in ("url", "identity", "token_file")):
            raise ValueError("brain config requires URL, identity and token file strings")
        self.base = config["url"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
            raise ValueError("brain URL must use HTTPS or loopback HTTP")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid brain URL")
        self.identity = config["identity"]
        if not all(33 <= ord(char) <= 126 for char in self.identity):
            raise ValueError("brain identity must contain printable ASCII without spaces")
        token_file = Path(config["token_file"]).expanduser()
        mode = token_file.lstat()
        if not stat.S_ISREG(mode.st_mode) or (os.name != "nt" and mode.st_mode & 0o077):
            raise ValueError("brain token must be a private regular file")
        self.token = token_file.read_text().strip()
        if not self.token or not all(33 <= ord(char) <= 126 for char in self.token):
            raise ValueError("brain token must contain printable ASCII without spaces")
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, data=None):
        request = urllib.request.Request(self.base + "/v1/brain/" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + self.token, "X-Brain-Client": self.identity,
                     "Content-Type": "application/json"})
        try:
            with self.http.open(request, timeout=25) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise RuntimeError("brain result exceeded limit")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # An error body can reflect credentials sent in the request.
            exc.close()
            raise RuntimeError(f"brain HTTP {exc.code}: request failed") from None
        except (TimeoutError, ConnectionError, urllib.error.URLError, http.client.HTTPException):
            raise RuntimeError("Brain connection failed. For a save, the result is unknown. "
                               "Retry with the same arguments.") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError("Brain returned an invalid reply. For a save, the result is unknown. "
                               "Retry with the same arguments.") from None


def dispatch(client, message):
    method = message.get("method")
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolError(-32602, "parameters must be an object")
    if method == "initialize":
        version = params.get("protocolVersion")
        if version is not None and not isinstance(version, str):
            raise ProtocolError(-32602, "protocol version must be a string")
        return {"protocolVersion": version if version in {"2024-11-05", "2025-03-26", "2025-06-18"} else "2025-06-18",
                "capabilities": {"tools": {}}, "serverInfo": {"name": "berry-brain", "version": package_version("berry-brain")},
                "instructions": client.request("tools")["instructions"]}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": client.request("tools")["tools"]}
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ProtocolError(-32602, "tool arguments must be an object")
        if not isinstance(name, str) or not name.startswith("brain_") or not name[6:].isascii() or not name[6:].isalpha():
            raise ProtocolError(-32602, "unknown brain tool")
        try:
            # The shared engine validates action names and current access rights.
            result = client.request(name.removeprefix("brain_"), arguments)
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, separators=(",", ":"))}], "isError": False}
        except RuntimeError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    raise ProtocolError(-32601, "method not found")


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
        from .local import LocalClient, default_directory
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
        request_id = None
        try:
            if len(line) > MAX_BYTES:
                # Discard the rest of this frame. Its tail is not a new request.
                while line and not line.endswith(b"\n"):
                    line = sys.stdin.buffer.readline(MAX_BYTES + 1)
                raise ProtocolError(-32600, "request exceeded limit")
            message = json.loads(line)
            if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                    or not isinstance(message.get("method"), str)
                    or ("id" in message and type(message["id"]) not in (str, int))):
                raise ProtocolError(-32600, "invalid request")
            if "id" not in message:
                continue
            request_id = message["id"]
            result = dispatch(client, message)
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception as exc:
            if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
                code, detail = -32700, "parse error"
            elif isinstance(exc, ProtocolError):
                code, detail = exc.code, str(exc)
            else:
                code, detail = -32603, "internal error"
            reply = {"jsonrpc": "2.0", "id": request_id,
                     "error": {"code": code, "message": detail}}
        print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
