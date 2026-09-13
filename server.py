import argparse
import asyncio
import functools
import hmac
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from enum import Enum
from contextlib import contextmanager, closing
from collections.abc import Iterator
from typing import Annotated, Any, Callable, Literal


IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
MEMORY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_UNSET = object()
RESERVED_METADATA_KEYS = frozenset(
    {"app_id", "agent_id", "user_id", "run_id", "session_id", "source_run_id"}
)
MAX_USER_ID_CHARS = 512
MAX_QUERY_CHARS = 16_000
MAX_MESSAGE_CHARS = 50_000
MAX_MESSAGES = 64
MAX_MESSAGES_CHARS = 256_000
MAX_METADATA_BYTES = 16_384
MAX_METADATA_DEPTH = 4
MAX_REQUEST_BODY_BYTES = 512 * 1024
MAX_RESULT_ROWS = 256
MAX_RESULT_MEMORY_CHARS = 50_000
MAX_RESULT_EVENT_CHARS = 64
MAX_RESULT_TIMESTAMP_CHARS = 128
AUTH_PATH = Path("/run/secrets/berry-memory-auth.env")
HISTORY_PATH = Path("/data/history.db")
INGESTION_PATH = Path("/data/ingestions.db")
QDRANT_URL = "http://qdrant:6333"
LLM_BASE_URL = os.getenv(
    "BERRY_LLM_BASE_URL", ""
).rstrip("/")
EMBED_MODEL = "local/embeddinggemma:latest"
EMBED_DIMS = 768
COLLECTION_NAME = "berry_memories_v4"
SERVICE_PORT = 8080

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


HEALTH_HTTP = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    NoRedirectHandler(),
)


class RequestBodyLimitMiddleware:
    def __init__(self, app: Any, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        try:
            declared = _declared_content_length(scope.get("headers", []))
        except ValueError:
            await _send_json_error(send, 400, "invalid Content-Length")
            return
        if declared is not None and declared > self.max_bytes:
            await _send_json_error(send, 413, "request body too large")
            return

        messages: list[dict[str, Any]] = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") != "http.request":
                break
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                await _send_json_error(send, 413, "request body too large")
                return
            if not message.get("more_body", False):
                break

        index = 0

        async def replay_receive() -> dict[str, Any]:
            nonlocal index
            if index >= len(messages):
                return {"type": "http.disconnect"}
            message = messages[index]
            index += 1
            return message

        await self.app(scope, replay_receive, send)


def _declared_content_length(headers: Any) -> int | None:
    values = [
        value
        for name, value in headers
        if isinstance(name, bytes) and name.lower() == b"content-length"
    ]
    if not values:
        return None
    if len(values) != 1 or not re.fullmatch(rb"[0-9]+", values[0]):
        raise ValueError("invalid Content-Length")
    return int(values[0])


async def _send_json_error(send: Any, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class AuthFailure(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("unauthorized" if status_code == 401 else "identity mismatch")


class BackendContractError(RuntimeError):
    pass


class FactScope(str, Enum):
    PROFILE = "profile"
    ROOM = "room"
    THREAD = "thread"


class IngestionBusy(RuntimeError):
    pass


class IngestionStore:
    """Durable idempotency receipts for agent-selected fact saves."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with self._connect() as database:
            database.execute("pragma journal_mode = wal")
            database.execute(
                """
                create table if not exists ingestion_receipts (
                    app_id text not null,
                    source_run_id text not null,
                    state text not null check (state in ('processing', 'retryable', 'done')),
                    response_json text,
                    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    primary key (app_id, source_run_id)
                )
                """
            )
            database.execute(
                "update ingestion_receipts set state = 'retryable', updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') where state = 'processing'"
            )
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as database:
            with database:
                yield database

    def execute(
        self,
        app_id: str,
        source_run_id: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._connect() as database:
            database.execute("begin immediate")
            row = database.execute(
                "select state, response_json from ingestion_receipts "
                "where app_id = ? and source_run_id = ?",
                (app_id, source_run_id),
            ).fetchone()
            if row is not None and row[0] == "done":
                database.commit()
                return json.loads(row[1])
            if row is not None and row[0] == "processing":
                database.commit()
                raise IngestionBusy("memory ingestion is already processing")
            database.execute(
                "insert into ingestion_receipts(app_id, source_run_id, state) "
                "values (?, ?, 'processing') "
                "on conflict(app_id, source_run_id) do update set "
                "state = 'processing', response_json = null, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                (app_id, source_run_id),
            )
            database.commit()

        try:
            result = operation()
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            with self._connect() as database:
                database.execute(
                    "update ingestion_receipts set state = 'retryable', "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "where app_id = ? and source_run_id = ? and state = 'processing'",
                    (app_id, source_run_id),
                )
            raise

        with self._connect() as database:
            changed = database.execute(
                "update ingestion_receipts set state = 'done', response_json = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "where app_id = ? and source_run_id = ? and state = 'processing'",
                (encoded, app_id, source_run_id),
            ).rowcount
        if changed != 1:
            raise RuntimeError("memory ingestion receipt lost its processing claim")
        return result


class MemoryAuth:
    def __init__(self, path: Path) -> None:
        identities: dict[str, str] = {}
        tokens: set[str] = set()
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            details = os.fstat(descriptor)
            mode = stat.S_IMODE(details.st_mode)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or mode not in {0o400, 0o600}
                or details.st_uid != os.geteuid()
            ):
                raise RuntimeError(
                    "Berry Memory auth file must be a single-link regular file "
                    f"owned by uid {os.geteuid()} with mode 0400 or 0600: {path}"
                )
            with os.fdopen(descriptor, encoding="ascii") as auth_file:
                descriptor = -1
                lines = auth_file.read().splitlines()
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"Berry Memory auth file is not ASCII: {path}") from exc
        except OSError as exc:
            raise RuntimeError(
                f"Berry Memory auth file cannot be opened safely: {path}"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        for line_number, line in enumerate(lines, start=1):
            identity, separator, token = line.partition("=")
            if (
                separator != "="
                or not IDENTITY_RE.fullmatch(identity)
                or not TOKEN_RE.fullmatch(token)
                or identity in identities
                or token in tokens
            ):
                raise RuntimeError(
                    f"invalid Berry Memory auth entry at {path}:{line_number}"
                )
            identities[identity] = token
            tokens.add(token)
        if not identities:
            raise RuntimeError(f"Berry Memory auth file has no identities: {path}")
        self._identities = identities

    @classmethod
    def load(cls) -> "MemoryAuth":
        return cls(AUTH_PATH)

    def authenticate(self, authorization: str, claimed_identity: str) -> str:
        scheme, separator, presented = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not TOKEN_RE.fullmatch(presented)
        ):
            raise AuthFailure(401)
        identity = ""
        for candidate, expected in self._identities.items():
            if hmac.compare_digest(presented, expected):
                identity = candidate
        if not identity:
            raise AuthFailure(401)
        if not claimed_identity or not _constant_time_text_equal(
            claimed_identity, identity
        ):
            raise AuthFailure(403)
        return identity


class DisabledLlm:
    """Mem0 requires an LLM object, but this service only permits raw storage."""

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def generate_response(self, *args: Any, **kwargs: Any) -> Any:
        raise BackendContractError("generation is disabled; the main agent selects memory facts")


class Mem0Backend:
    def __init__(self) -> None:
        if not LLM_BASE_URL:
            raise RuntimeError("BERRY_LLM_BASE_URL is required for server mode")
        token = os.getenv("BERRY_LLM_API_KEY", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise RuntimeError("BERRY_LLM_API_KEY is missing or invalid")
        os.environ["OPENAI_API_KEY"] = token
        from mem0 import Memory  # type: ignore
        from mem0.utils.factory import LlmFactory  # type: ignore
        LlmFactory.register_provider("berry_disabled", "server.DisabledLlm")
        self.mem = Memory.from_config(_mem0_config())
        self.receipts = IngestionStore(INGESTION_PATH)
        self.write_lock = threading.Lock()

    def add(
        self,
        *,
        user_id: str,
        app_id: str,
        messages: list[dict[str, str]],
        source_run_id: str = "",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _validate_add_ownership(user_id, messages, metadata or {}, source_run_id)
        meta = {**(metadata or {}), "app_id": app_id}
        if source_run_id:
            meta["source_run_id"] = source_run_id
        meta["attributed_to"] = meta.get("attributed_to", "user")
        return {"results": self._store_fact(user_id, app_id, messages[0]["content"], meta)}

    def _store_fact(self, owner: str, app_id: str, text: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        # Mem0's raw add API avoids a second model extraction. Exact-content
        # lookup also recovers a write completed before its receipt was saved.
        with self.write_lock:
            filters = {**_scope_filters(owner, app_id),
                       "hash": hashlib.md5(text.encode(), usedforsecurity=False).hexdigest(),
                       "attributed_to": metadata["attributed_to"]}
            found = _mem0_results(self.mem.get_all(filters=filters, top_k=100), app_id,
                                  user_id=owner, require_owner=True)
            existing = [row for row in found if row["memory"] == text]
            if existing:
                return existing[:1]
            data = self.mem.add(
                [{"role": metadata["attributed_to"], "content": text}],
                user_id=owner, agent_id=app_id, metadata=metadata, infer=False,
            )
            rows = _mem0_results(data, app_id)
            if not rows:
                raise BackendContractError("explicit storage returned no memory")
            for row in rows:
                row["owner_id"] = owner
                row["attributed_to"] = metadata["attributed_to"]
            return rows

    def search(self, *, user_id: str, query: str, app_id: str, limit: int = 8) -> dict[str, Any]:
        filters = _scope_filters(user_id, app_id)
        data = self.mem.search(query=query, filters=filters, top_k=limit)
        return {
            "results": _mem0_results(
                data,
                app_id,
                user_id=user_id,
                require_owner=True,
            )
        }

    def list(self, *, user_id: str, app_id: str, limit: int = 100) -> dict[str, Any]:
        filters = _scope_filters(user_id, app_id)
        data = self.mem.get_all(filters=filters, top_k=limit)
        return {
            "results": _mem0_results(
                data,
                app_id,
                user_id=user_id,
                require_owner=True,
            )
        }

    def delete(self, memory_id: str, app_id: str, user_id: str) -> dict[str, Any]:
        memory = self.mem.get(memory_id)
        if not (
            _memory_belongs_to(memory, app_id)
            & _memory_user_matches(memory, user_id)
        ):
            return {"deleted": False}
        self.mem.delete(memory_id=memory_id)
        return {"deleted": True}

def backend() -> Any:
    return Mem0Backend()


def _fact_owner(scope: FactScope, metadata: dict[str, Any]) -> str | None:
    room = _validated_identifier(metadata.get("room_id"), "room_id", 256)
    thread = _validated_identifier(metadata.get("thread_id"), "thread_id", 256)
    if scope == FactScope.ROOM:
        return f"room:{room}"
    if scope == FactScope.THREAD:
        return f"thread:{room}:{thread}"
    profile = metadata.get("profile_id")
    if not isinstance(profile, str) or not profile or profile.startswith("agent-"):
        return None
    return "profile:" + _validated_identifier(profile, "profile_id", 256)


def _validate_add_ownership(
    user_id: str, messages: list[dict[str, str]], metadata: dict[str, Any], source_run_id: str,
) -> None:
    if metadata.get("explicit") is not True:
        raise ValueError("only agent-selected facts are accepted; use an explicit save")
    for key in ("room_id", "thread_id", "profile_id"):
        if key in metadata:
            _validated_identifier(metadata[key], key, 256)
    if source_run_id.startswith("extract:"):
        raise ValueError("source_run_id uses a reserved extraction prefix")
    if len(messages) != 1:
        raise ValueError("an explicit save must contain exactly one fact")
    if metadata.get("attributed_to", "user") not in ("user", "assistant"):
        raise ValueError("explicit save has invalid source")
    scope = FactScope(metadata.get("scope"))
    # Older explicit clients can omit provenance. If supplied, it must
    # agree with the target owner; never let metadata redirect a save.
    if user_id.split(":", 1)[0] != scope.value:
        raise ValueError("explicit save scope does not match its owner")
    if scope == FactScope.PROFILE and "profile_id" in metadata:
        profile = metadata["profile_id"]
        if profile.startswith("agent-") or user_id != "profile:" + profile:
            raise ValueError("explicit save does not belong to the requesting person")
    if scope != FactScope.PROFILE and "room_id" in metadata:
        if user_id != _fact_owner(scope, metadata):
            raise ValueError("explicit save does not match its room or thread")


def _mem0_config() -> dict[str, Any]:
    return {
        "history_db_path": str(HISTORY_PATH),
        "llm": {"provider": "berry_disabled"},
        "embedder": {
            "provider": "openai",
            "config": {
                "model": EMBED_MODEL,
                "openai_base_url": LLM_BASE_URL,
                "embedding_dims": EMBED_DIMS,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "url": QDRANT_URL,
                "embedding_model_dims": EMBED_DIMS,
                "collection_name": COLLECTION_NAME,
            },
        },
    }

def _require_healthy_history(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or path.is_symlink() or details.st_nlink != 1:
        raise RuntimeError("Mem0 history must be a single-link regular file")
    uri = f"file:{urllib.parse.quote(str(path.absolute()), safe='/')}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as history:
        if history.execute("pragma quick_check(1)").fetchall() != [("ok",)]:
            raise RuntimeError("Mem0 history quick_check failed")


def runtime_health_check() -> None:
    _require_healthy_history(
        HISTORY_PATH
    )
    _require_healthy_history(INGESTION_PATH)
    auth = MemoryAuth.load()
    identity = sorted(auth._identities)[0]
    token = auth._identities[identity]
    query = urllib.parse.urlencode(
        {"user_id": "health:berry-memory", "app_id": identity, "limit": "1"}
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{SERVICE_PORT}/v1/memories?{query}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Berry-App": identity,
        },
    )
    with HEALTH_HTTP.open(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(
                f"Berry Memory readiness returned HTTP {response.status}"
            )
        memories = json.loads(response.read())
    if not isinstance(memories, dict) or not isinstance(memories.get("results"), list):
        raise RuntimeError("Berry Memory readiness response is malformed")

    gateway_request = urllib.request.Request(
        f"{LLM_BASE_URL.removesuffix('/v1')}/models",
        headers={
            "Authorization": f"Bearer {os.environ['BERRY_LLM_API_KEY']}",
            "X-Berry-App": "berry-memory",
        },
    )
    with HEALTH_HTTP.open(gateway_request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(
                f"LLM Gateway model catalog returned HTTP {response.status}"
            )
        catalog = json.loads(response.read())
    rows = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("LLM Gateway model catalog is malformed")
    available = {
        f"{row.get('source')}/{row.get('model')}"
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("source"), str)
        and isinstance(row.get("model"), str)
    }
    required = {
        EMBED_MODEL,
    }
    missing = sorted(name for name in required if name not in available)
    if missing:
        raise RuntimeError(
            f"required Gateway models are unavailable: {', '.join(missing)}"
        )


def _validated_text(value: str, name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars or "\0" in value:
        raise ValueError(
            f"{name} must contain 1-{max_chars} characters without NUL"
        )
    return value


def _constant_time_text_equal(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except UnicodeEncodeError:
        return False


def _validated_identifier(value: str, name: str, max_chars: int) -> str:
    _validated_text(value, name, max_chars)
    if any(character.isspace() for character in value):
        raise ValueError(f"{name} must not contain whitespace")
    return value


def _validated_app_id(value: str) -> str:
    if not IDENTITY_RE.fullmatch(value):
        raise ValueError("app_id must be a valid Berry App Identity")
    return value


def _validate_json_tree(value: Any, depth: int = 0) -> None:
    if depth > MAX_METADATA_DEPTH:
        raise ValueError(f"metadata nesting exceeds {MAX_METADATA_DEPTH}")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > 4_096 or "\0" in value:
            raise ValueError("metadata strings must contain at most 4096 characters without NUL")
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ValueError("metadata lists may contain at most 64 items")
        for item in value:
            _validate_json_tree(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("metadata objects may contain at most 32 fields")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 128
                or "\0" in key
            ):
                raise ValueError("metadata keys must contain 1-128 characters without NUL")
            _validate_json_tree(item, depth + 1)
        return
    raise ValueError("metadata must contain only JSON values")


def _validated_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        raise ValueError("metadata must be a nonempty object")
    reserved = sorted(RESERVED_METADATA_KEYS.intersection(value))
    if reserved:
        raise ValueError(f"metadata contains reserved ownership keys: {', '.join(reserved)}")
    _validate_json_tree(value)
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be valid JSON") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError(f"metadata exceeds {MAX_METADATA_BYTES} encoded bytes")
    return value


def create_app(
    *,
    memory_backend: Any | None = None,
    memory_auth: MemoryAuth | None = None,
    ingestion_store: IngestionStore | None = None,
) -> Any:
    from fastapi import Depends, FastAPI, Header, HTTPException, Path as ApiPath, Query
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
    from starlette.concurrency import run_in_threadpool

    class StrictBody(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

    class Message(StrictBody):
        role: Literal["user", "assistant"]
        content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

        @field_validator("content")
        @classmethod
        def validate_content(cls, value: str) -> str:
            return _validated_text(value, "message content", MAX_MESSAGE_CHARS)

    class AddRequest(StrictBody):
        user_id: str = Field(min_length=1, max_length=MAX_USER_ID_CHARS)
        app_id: str = Field(min_length=2, max_length=64)
        source_run_id: str | None = Field(default=None, min_length=1, max_length=256)
        session_id: str | None = Field(default=None, min_length=1, max_length=512)
        messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
        metadata: dict[str, Any]

        @field_validator("user_id")
        @classmethod
        def validate_user_id(cls, value: str) -> str:
            return _validated_identifier(value, "user_id", MAX_USER_ID_CHARS)

        @field_validator("app_id")
        @classmethod
        def validate_app_id(cls, value: str) -> str:
            return _validated_app_id(value)

        @field_validator("source_run_id")
        @classmethod
        def validate_source_run_id(cls, value: str | None) -> str | None:
            if value is not None:
                return _validated_identifier(value, "source_run_id", 256)
            return None

        @field_validator("session_id")
        @classmethod
        def validate_session_id(cls, value: str | None) -> str | None:
            if value is not None:
                return _validated_identifier(value, "session_id", 512)
            return None

        @field_validator("metadata")
        @classmethod
        def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
            return _validated_metadata(value)

        @model_validator(mode="after")
        def validate_message_total(self):
            for field, value, maximum in (
                ("source_run_id", self.source_run_id, 256),
                ("session_id", self.session_id, 512),
            ):
                if field in self.model_fields_set and value is None:
                    raise ValueError(
                        f"{field} must be omitted or contain 1-{maximum} characters"
                    )
            if (self.source_run_id is None) != (self.session_id is None):
                raise ValueError(
                    "source_run_id and session_id must be supplied together for durable saves"
                )
            if sum(len(message.content) for message in self.messages) > MAX_MESSAGES_CHARS:
                raise ValueError(
                    f"combined message content exceeds {MAX_MESSAGES_CHARS} characters"
                )
            _validate_add_ownership(
                self.user_id, [message.model_dump() for message in self.messages],
                self.metadata, self.source_run_id or "",
            )
            return self

    class SearchRequest(StrictBody):
        user_id: str = Field(min_length=1, max_length=MAX_USER_ID_CHARS)
        app_id: str = Field(min_length=2, max_length=64)
        query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
        limit: int = Field(default=8, ge=1, le=100)

        @field_validator("user_id")
        @classmethod
        def validate_user_id(cls, value: str) -> str:
            return _validated_identifier(value, "user_id", MAX_USER_ID_CHARS)

        @field_validator("app_id")
        @classmethod
        def validate_app_id(cls, value: str) -> str:
            return _validated_app_id(value)

        @field_validator("query")
        @classmethod
        def validate_query(cls, value: str) -> str:
            return _validated_text(value, "query", MAX_QUERY_CHARS)

    class StrictQuery(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class ListQuery(StrictQuery):
        user_id: str = Field(min_length=1, max_length=MAX_USER_ID_CHARS)
        app_id: str = Field(min_length=2, max_length=64)
        limit: int = Field(default=100, ge=1, le=100)

        @field_validator("user_id")
        @classmethod
        def validate_user_id(cls, value: str) -> str:
            return _validated_identifier(value, "user_id", MAX_USER_ID_CHARS)

        @field_validator("app_id")
        @classmethod
        def validate_app_id(cls, value: str) -> str:
            return _validated_app_id(value)

    class DeleteQuery(StrictQuery):
        user_id: str = Field(min_length=1, max_length=MAX_USER_ID_CHARS)
        app_id: str = Field(min_length=2, max_length=64)

        @field_validator("user_id")
        @classmethod
        def validate_user_id(cls, value: str) -> str:
            return _validated_identifier(value, "user_id", MAX_USER_ID_CHARS)

        @field_validator("app_id")
        @classmethod
        def validate_app_id(cls, value: str) -> str:
            return _validated_app_id(value)

    app = FastAPI(title="Berry Memory")
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=MAX_REQUEST_BODY_BYTES,
    )
    authenticator = (
        memory_auth if memory_auth is not None else MemoryAuth.load()
    )
    mem = memory_backend if memory_backend is not None else backend()
    receipts = ingestion_store
    if receipts is None and memory_backend is None:
        receipts = mem.receipts

    def auth(
        authorization: str = Header(default=""),
        x_berry_app: str = Header(default="", alias="X-Berry-App"),
    ) -> str:
        try:
            return authenticator.authenticate(authorization, x_berry_app)
        except AuthFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def scoped_identity(identity: str, claim: object = _UNSET) -> str:
        try:
            return _claimed_identity(identity, claim)
        except AuthFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"ok": "true"}

    @app.post("/v1/memories")
    async def add(body: AddRequest, identity: str = Depends(auth)) -> dict[str, Any]:
        identity = scoped_identity(identity, body.app_id)
        operation = functools.partial(
            mem.add,
            user_id=body.user_id,
            app_id=identity,
            source_run_id=body.source_run_id or "",
            session_id=body.session_id or "",
            messages=[message.model_dump() for message in body.messages],
            metadata=body.metadata,
        )
        try:
            if receipts is not None and body.source_run_id is not None:
                return await run_in_threadpool(
                    receipts.execute,
                    identity,
                    body.source_run_id,
                    operation,
                )
            return await run_in_threadpool(operation)
        except IngestionBusy as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except BackendContractError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/memories/search")
    async def search(
        body: SearchRequest, identity: str = Depends(auth)
    ) -> dict[str, Any]:
        identity = scoped_identity(identity, body.app_id)
        return await run_in_threadpool(
            functools.partial(
                mem.search,
                user_id=body.user_id,
                query=body.query,
                app_id=identity,
                limit=body.limit,
            )
        )

    @app.get("/v1/memories")
    def list_memories(
        query: Annotated[ListQuery, Query()],
        identity: str = Depends(auth),
    ) -> dict[str, Any]:
        identity = scoped_identity(identity, query.app_id)
        return mem.list(user_id=query.user_id, app_id=identity, limit=query.limit)

    @app.delete("/v1/memories/{memory_id}")
    def delete(
        memory_id: Annotated[
            str,
            ApiPath(
                min_length=1,
                max_length=256,
                pattern=MEMORY_ID_RE.pattern,
            ),
        ],
        query: Annotated[DeleteQuery, Query()],
        identity: str = Depends(auth),
    ) -> dict[str, Any]:
        identity = scoped_identity(identity, query.app_id)
        return mem.delete(memory_id, identity, query.user_id)

    if memory_backend is None:
        from brain import install
        install(app, auth)
    return app


async def _body_limiter_self_check() -> None:
    async def exercise(
        *,
        headers: list[tuple[bytes, bytes]],
        chunks: list[tuple[bytes, bool]],
        path: str = "/v1/memories",
    ) -> tuple[list[int], int, int]:
        downstream_calls = 0
        downstream_bytes = 0
        responses: list[dict[str, Any]] = []
        requests = [
            {"type": "http.request", "body": body, "more_body": more_body}
            for body, more_body in chunks
        ]

        async def receive() -> dict[str, Any]:
            if requests:
                return requests.pop(0)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            responses.append(message)

        async def downstream(
            _scope: dict[str, Any], downstream_receive: Any, downstream_send: Any
        ) -> None:
            nonlocal downstream_calls, downstream_bytes
            downstream_calls += 1
            while True:
                message = await downstream_receive()
                if message.get("type") != "http.request":
                    break
                downstream_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            await downstream_send({"type": "http.response.start", "status": 204, "headers": []})
            await downstream_send({"type": "http.response.body", "body": b""})

        middleware = RequestBodyLimitMiddleware(downstream)
        await middleware(
            {"type": "http", "path": path, "method": "GET", "headers": headers},
            receive,
            send,
        )
        statuses = [
            message["status"]
            for message in responses
            if message.get("type") == "http.response.start"
        ]
        return statuses, downstream_calls, downstream_bytes

    exact = b"x" * MAX_REQUEST_BODY_BYTES
    assert await exercise(
        headers=[(b"content-length", str(len(exact)).encode("ascii"))],
        chunks=[(exact, False)],
    ) == ([204], 1, MAX_REQUEST_BODY_BYTES)
    assert await exercise(
        headers=[
            (b"content-length", str(MAX_REQUEST_BODY_BYTES + 1).encode("ascii"))
        ],
        chunks=[],
    ) == ([413], 0, 0)
    assert await exercise(
        headers=[(b"transfer-encoding", b"chunked")],
        chunks=[(exact, True), (b"x", False)],
    ) == ([413], 0, 0)
    assert await exercise(
        headers=[
            (b"content-length", str(MAX_REQUEST_BODY_BYTES + 1).encode("ascii"))
        ],
        chunks=[(b"", False)],
        path="/health",
    ) == ([413], 0, 0)
    assert await exercise(
        headers=[(b"content-length", b"0")],
        chunks=[(b"", False)],
        path="/health",
    ) == ([204], 1, 0)


async def api_self_check() -> None:
    class FakeBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        def add(self, **kwargs: Any) -> dict[str, list]:
            self.calls.append(("add", kwargs))
            return {"results": []}

        def search(self, **kwargs: Any) -> dict[str, list]:
            self.calls.append(("search", kwargs))
            return {"results": []}

        def list(self, **kwargs: Any) -> dict[str, list]:
            self.calls.append(("list", kwargs))
            return {"results": []}

        def delete(self, *args: str) -> dict[str, bool]:
            self.calls.append(("delete", args))
            return {"deleted": False}

    with tempfile.TemporaryDirectory() as directory:
        token = "a" * 64
        auth_path = Path(directory) / "auth.env"
        auth_path.write_text(f"berry-agents={token}\n", encoding="ascii")
        auth_path.chmod(0o600)
        fake = FakeBackend()
        app = create_app(
            memory_backend=fake,
            memory_auth=MemoryAuth(auth_path),
        )

        async def request(
            method: str,
            path: str,
            *,
            query: dict[str, str] | None = None,
            payload: Any = _UNSET,
            raw_body: bytes | None = None,
            authenticated: bool = True,
        ) -> int:
            if payload is not _UNSET:
                raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            body = raw_body or b""
            headers = [
                (b"host", b"memory.test"),
                (b"content-length", str(len(body)).encode("ascii")),
            ]
            if payload is not _UNSET or raw_body is not None:
                headers.append((b"content-type", b"application/json"))
            if authenticated:
                headers.extend(
                    [
                        (b"authorization", f"Bearer {token}".encode("ascii")),
                        (b"x-berry-app", b"berry-agents"),
                    ]
                )
            incoming = [
                {"type": "http.request", "body": body, "more_body": False}
            ]
            outgoing: list[dict[str, Any]] = []

            async def receive() -> dict[str, Any]:
                if incoming:
                    return incoming.pop(0)
                return {"type": "http.disconnect"}

            async def send(message: dict[str, Any]) -> None:
                outgoing.append(message)

            query_string = urllib.parse.urlencode(query or {}).encode("ascii")
            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode("ascii"),
                    "query_string": query_string,
                    "headers": headers,
                    "client": ("127.0.0.1", 12345),
                    "server": ("memory.test", 80),
                    "root_path": "",
                },
                receive,
                send,
            )
            statuses = [
                message["status"]
                for message in outgoing
                if message.get("type") == "http.response.start"
            ]
            assert len(statuses) == 1
            return statuses[0]

        add_payload = {
            "user_id": "profile:sample-user",
            "app_id": "berry-agents",
            "messages": [{"role": "user", "content": "Remember concise replies"}],
            "metadata": {"scope": "profile", "explicit": True},
        }
        assert await request("POST", "/v1/memories", payload=add_payload) == 200
        assert fake.calls[-1][0] == "add"
        assert await request(
            "POST",
            "/v1/memories",
            payload={**add_payload, "source_run_id": None},
        ) == 422
        turn_payload = {
            **add_payload,
            "source_run_id": "run-123",
            "session_id": "matrix:!room:test:$thread",
        }
        assert await request("POST", "/v1/memories", payload=turn_payload) == 200
        assert fake.calls[-1] == (
            "add",
            {
                "user_id": "profile:sample-user",
                "app_id": "berry-agents",
                "source_run_id": "run-123",
                "session_id": "matrix:!room:test:$thread",
                "messages": [{"role": "user", "content": "Remember concise replies"}],
                "metadata": {"scope": "profile", "explicit": True},
            },
        )
        calls_before_invalid = len(fake.calls)
        for changed in [
            {"source_run_id": "extract:run-123"},
            {"metadata": {"scope": "profile", "explicit": "true"}},
            {"metadata": {"scope": "profile", "explicit": True, "profile_id": "second-user"}},
            {"metadata": {"scope": "profile", "explicit": True, "room_id": None}},
            {"metadata": {"scope": "room", "room_id": "!room:test", "thread_id": "$thread"}},
        ]:
            assert await request("POST", "/v1/memories", payload={**turn_payload, **changed}) == 422
        assert len(fake.calls) == calls_before_invalid
        assert await request(
            "POST",
            "/v1/memories",
            payload={**add_payload, "source_run_id": "run-123"},
        ) == 422

        delete_query = {
            "user_id": "profile:sample-user",
            "app_id": "berry-agents",
        }
        assert await request(
            "DELETE",
            "/v1/memories/memory-ID_9",
            query=delete_query,
        ) == 200
        assert fake.calls[-1] == (
            "delete",
            ("memory-ID_9", "berry-agents", "profile:sample-user"),
        )
        assert await request(
            "DELETE",
            "/v1/memories/memory-ID_9",
            query={"user_id": "profile:sample-user"},
        ) == 422
        assert await request(
            "DELETE",
            "/v1/memories/memory-ID_9",
            query={**delete_query, "app_id": "other-memory-client"},
        ) == 403
        assert await request(
            "DELETE",
            "/v1/memories/bad.id",
            query=delete_query,
        ) == 422
        assert await request(
            "DELETE",
            f"/v1/memories/{'x' * 257}",
            query=delete_query,
        ) == 422
        assert await request(
            "DELETE",
            "/v1/memories/memory-ID_9",
            query={**delete_query, "unknown": "field"},
        ) == 422

        strict_search = {
            "user_id": "profile:sample-user",
            "app_id": "berry-agents",
            "query": "concise",
            "limit": "8",
        }
        assert await request(
            "POST",
            "/v1/memories/search",
            payload=strict_search,
        ) == 422
        assert await request(
            "POST",
            "/v1/memories",
            raw_body=b"{not-json",
        ) == 422
        assert await request(
            "POST",
            "/v1/memories",
            payload=add_payload,
            authenticated=False,
        ) == 401
        assert await request(
            "POST",
            "/v1/memories",
            raw_body=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
        ) == 413
        assert await request("GET", "/health") == 200


def self_check() -> None:
    asyncio.run(_body_limiter_self_check())
    assert _validated_identifier("profile:sample-user", "user_id", MAX_USER_ID_CHARS) == "profile:sample-user"
    with tempfile.TemporaryDirectory() as history_tmp:
        history_path = Path(history_tmp) / "history.db"
        with sqlite3.connect(history_path) as history:
            history.execute("create table health(value text)")
        _require_healthy_history(history_path)
        history_link = Path(history_tmp) / "history-link.db"
        os.link(history_path, history_link)
        try:
            _require_healthy_history(history_path)
            raise AssertionError("hard-linked Mem0 history accepted")
        except RuntimeError:
            pass
    with tempfile.TemporaryDirectory() as ingestion_tmp:
        receipts = IngestionStore(Path(ingestion_tmp) / "ingestions.db")
        calls = 0

        def ingest() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"results": [{"id": "memory-1"}]}

        first = receipts.execute("berry-agents", "run-1", ingest)
        second = receipts.execute("berry-agents", "run-1", ingest)
        assert first == second
        assert calls == 1

        def fail_once() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            raise RuntimeError("transient test failure")

        try:
            receipts.execute("berry-agents", "run-2", fail_once)
            raise AssertionError("failed ingestion was accepted")
        except RuntimeError as exc:
            assert str(exc) == "transient test failure"
        assert receipts.execute("berry-agents", "run-2", ingest) == first
    assert (
        NoRedirectHandler().redirect_request(None, None, 302, "", {}, "http://invalid")
        is None
    )
    assert MEMORY_ID_RE.fullmatch("A_valid-memory_ID-9")
    assert not MEMORY_ID_RE.fullmatch("../foreign")
    assert not MEMORY_ID_RE.fullmatch("x" * 257)
    assert _validated_text("sample note", "query", MAX_QUERY_CHARS) == "sample note"
    assert _validated_metadata({"scope": "profile", "explicit": True}) == {
        "scope": "profile",
        "explicit": True,
    }
    for invalid_metadata in (
        {},
        {"app_id": "spoofed"},
        {"session_id": "spoofed"},
        {"source_run_id": "spoofed"},
        {"nested": [[[[["too deep"]]]]]},
    ):
        try:
            _validated_metadata(invalid_metadata)
            raise AssertionError("invalid metadata accepted")
        except ValueError:
            pass
    config = _mem0_config()
    assert config["llm"] == {"provider": "berry_disabled"}
    assert config["embedder"] == {
        "provider": "openai",
        "config": {
            "model": "local/embeddinggemma:latest",
            "openai_base_url": LLM_BASE_URL,
            "embedding_dims": 768,
        },
    }
    assert config["vector_store"]["config"]["embedding_model_dims"] == 768
    assert config["vector_store"]["config"]["collection_name"] == "berry_memories_v4"
    assert config["history_db_path"] == "/data/history.db"
    production_token = "a" * 64
    secondary_token = "b" * 64
    with tempfile.TemporaryDirectory() as directory:
        auth_path = Path(directory) / "auth.env"
        auth_path.write_text(
            f"berry-agents={production_token}\nother-memory-client={secondary_token}\n",
            encoding="ascii",
        )
        auth_path.chmod(0o600)
        auth = MemoryAuth(auth_path)
        assert (
            auth.authenticate(f"Bearer {production_token}", "berry-agents")
            == "berry-agents"
        )
        for authorization, identity, status in (
            (f"Bearer {production_token}", "other-memory-client", 403),
            (f"Bearer {secondary_token}", "", 403),
            (f"Bearer {'c' * 64}", "berry-agents", 401),
            ("", "berry-agents", 401),
        ):
            try:
                auth.authenticate(authorization, identity)
                raise AssertionError("invalid Memory identity authenticated")
            except AuthFailure as exc:
                assert exc.status_code == status
        linked_auth_path = Path(directory) / "auth-hardlink.env"
        os.link(auth_path, linked_auth_path)
        try:
            MemoryAuth(auth_path)
            raise AssertionError("hard-linked Memory auth file accepted")
        except RuntimeError as exc:
            assert "single-link regular file" in str(exc)
        linked_auth_path.unlink()
        auth_path.chmod(0o644)
        try:
            MemoryAuth(auth_path)
            raise AssertionError("over-permissive Memory auth file accepted")
        except RuntimeError as exc:
            assert "mode 0400 or 0600" in str(exc)
        auth_path.chmod(0o600)
        symlink_auth_path = Path(directory) / "auth-symlink.env"
        symlink_auth_path.symlink_to(auth_path)
        try:
            MemoryAuth(symlink_auth_path)
            raise AssertionError("symlinked Memory auth file accepted")
        except RuntimeError as exc:
            assert "cannot be opened safely" in str(exc)
        auth_path.write_text("berry-agents=NOT-HEX\n", encoding="ascii")
        try:
            MemoryAuth(auth_path)
            raise AssertionError("invalid Memory auth file accepted")
        except RuntimeError as exc:
            assert "invalid Berry Memory auth entry" in str(exc)

    assert _claimed_identity("berry-agents") == "berry-agents"
    try:
        _claimed_identity("berry-agents", "other-memory-client")
        raise AssertionError("cross-app claim accepted")
    except AuthFailure as exc:
        assert exc.status_code == 403
    rows = _mem0_results(
        {
            "results": [
                {
                    "id": "m1",
                    "memory": "left sample note",
                    "event": "ADD",
                    "metadata": {"app_id": "berry-agents"},
                    "score": 0.9,
                }
            ]
        },
        "berry-agents",
    )
    assert (
        rows[0]["id"] == "m1"
        and rows[0]["app_id"] == "berry-agents"
        and rows[0]["score"] == 0.9
    )
    owned_rows = _mem0_results(
        {
            "results": [
                {
                    "id": "owned",
                    "memory": "owned memory",
                    "agent_id": "berry-agents",
                    "user_id": "profile:sample-user",
                    "metadata": {"app_id": "berry-agents"},
                },
                {
                    "id": "wrong-user",
                    "memory": "other profile memory",
                    "agent_id": "berry-agents",
                    "user_id": "profile:other",
                    "metadata": {"app_id": "berry-agents"},
                },
                {
                    "id": "wrong-app",
                    "memory": "other app memory",
                    "agent_id": "other-memory-client",
                    "user_id": "profile:sample-user",
                    "metadata": {"app_id": "other-memory-client"},
                },
            ]
        },
        "berry-agents",
        user_id="profile:sample-user",
        require_owner=True,
    )
    assert [row["id"] for row in owned_rows] == ["owned"]
    optional_row = _mem0_results(
        {
            "results": [
                {
                    "id": "optional",
                    "memory": "valid optional fields",
                    "agent_id": "berry-agents",
                    "user_id": "profile:sample-user",
                    "metadata": {"app_id": "berry-agents"},
                }
            ]
        },
        "berry-agents",
        user_id="profile:sample-user",
        require_owner=True,
    )[0]
    assert optional_row["score"] is None
    assert optional_row["event"] == ""
    assert optional_row["updated_at"] == ""

    malformed_owned_rows = (
        None,
        {"id": "bad.id", "memory": "memory"},
        {"id": "valid-id", "memory": 7},
        {"id": "valid-id", "memory": "memory", "event": 7},
        {"id": "valid-id", "memory": "memory", "score": float("nan")},
        {"id": "valid-id", "memory": "memory", "score": True},
        {"id": "valid-id", "memory": "memory", "score": 10**10_000},
        {"id": "valid-id", "memory": "memory", "updated_at": 7},
        {
            "id": "valid-id",
            "memory": "memory",
            "app_id": "other-memory-client",
        },
    )
    for malformed in malformed_owned_rows:
        try:
            _mem0_results({"results": [malformed]}, "berry-agents")
            raise AssertionError("malformed Mem0 result accepted")
        except BackendContractError:
            pass
    try:
        _mem0_results({"results": "not-a-list"}, "berry-agents")
        raise AssertionError("malformed Mem0 envelope accepted")
    except BackendContractError:
        pass

    filtered_malformed_row = _mem0_results(
        {
            "results": [
                {
                    "id": "bad.id",
                    "agent_id": "berry-agents",
                    "user_id": "profile:other",
                    "metadata": {"app_id": "berry-agents"},
                }
            ]
        },
        "berry-agents",
        user_id="profile:sample-user",
        require_owner=True,
    )
    assert filtered_malformed_row == []

    class FakeMemory:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []
            self.search_rows: list[Any] = []
            self.list_rows: list[Any] = []
            self.rows: dict[str, dict[str, Any]] = {
                "owned": {
                    "id": "owned",
                    "agent_id": "sample-app",
                    "user_id": "@sample-user:example.org",
                    "metadata": {"app_id": "sample-app"},
                },
                "foreign": {
                    "id": "foreign",
                    "agent_id": "berry-agents",
                    "user_id": "@sample-user:example.org",
                    "metadata": {"app_id": "berry-agents"},
                },
                "other-user": {
                    "id": "other-user",
                    "agent_id": "sample-app",
                    "user_id": "@other:example.org",
                    "metadata": {"app_id": "sample-app"},
                },
            }

        def add(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, list]:
            self.calls.append(("add", {"messages": messages, **kwargs}))
            return {
                "results": [
                    {
                        "id": "added",
                        "memory": "remembered fact",
                        "event": "ADD",
                    }
                ]
            }

        def search(self, **kwargs: Any) -> dict[str, list]:
            self.calls.append(("search", kwargs))
            return {"results": self.search_rows}

        def get_all(self, **kwargs: Any) -> dict[str, list]:
            self.calls.append(("get_all", kwargs))
            return {"results": self.list_rows}

        def get(self, memory_id: str) -> dict[str, Any] | None:
            self.calls.append(("get", {"memory_id": memory_id}))
            return self.rows.get(memory_id)

        def delete(self, **kwargs: Any) -> None:
            self.calls.append(("delete", kwargs))

    fake = FakeMemory()
    mem0 = object.__new__(Mem0Backend)
    mem0.mem = fake
    mem0.write_lock = threading.Lock()
    added = mem0.add(
        user_id="profile:sample-user", app_id="sample-app",
        source_run_id="run-123", session_id="matrix:!room:example.org:$thread",
        messages=[{"role": "user", "content": "remember this"}],
        metadata={"explicit": True, "scope": "profile"},
    )
    assert added["results"][0]["id"] == "added"
    assert added["results"][0]["owner_id"] == "profile:sample-user"
    assert added["results"][0]["attributed_to"] == "user"
    assert fake.calls[0][0] == "get_all"
    assert fake.calls[1][0] == "add"
    assert fake.calls[1][1]["infer"] is False
    assert fake.calls[1][1]["user_id"] == "profile:sample-user"
    fake.calls.clear()
    searched = mem0.search(user_id="@sample-user:example.org", query="sample", app_id="sample-app", limit=7)
    listed = mem0.list(user_id="@sample-user:example.org", app_id="sample-app", limit=25)
    assert searched == {"results": []}
    assert listed == {"results": []}
    assert fake.calls[:2] == [
        (
            "search",
            {
                "query": "sample",
                "filters": {
                    "user_id": "@sample-user:example.org",
                    "agent_id": "sample-app",
                    "app_id": "sample-app",
                },
                "top_k": 7,
            },
        ),
        (
            "get_all",
            {
                "filters": {
                    "user_id": "@sample-user:example.org",
                    "agent_id": "sample-app",
                    "app_id": "sample-app",
                },
                "top_k": 25,
            },
        ),
    ]
    assert mem0.delete("owned", "sample-app", "@sample-user:example.org") == {
        "deleted": True
    }
    assert mem0.delete("foreign", "sample-app", "@sample-user:example.org") == {
        "deleted": False
    }
    assert mem0.delete("other-user", "sample-app", "@sample-user:example.org") == {
        "deleted": False
    }
    assert mem0.delete("missing", "sample-app", "@sample-user:example.org") == {
        "deleted": False
    }
    assert fake.calls[2:] == [
        ("get", {"memory_id": "owned"}),
        ("delete", {"memory_id": "owned"}),
        ("get", {"memory_id": "foreign"}),
        ("get", {"memory_id": "other-user"}),
        ("get", {"memory_id": "missing"}),
    ]
    fake.search_rows = [
        {
            "id": "owned-result",
            "memory": "owned result",
            "agent_id": "sample-app",
            "user_id": "@sample-user:example.org",
            "metadata": {"app_id": "sample-app"},
        },
        {
            "id": "foreign-result",
            "memory": "foreign result",
            "agent_id": "berry-agents",
            "user_id": "@sample-user:example.org",
            "metadata": {"app_id": "berry-agents"},
        },
    ]
    assert mem0.search(
        user_id="@sample-user:example.org",
        query="sample",
        app_id="sample-app",
        limit=7,
    ) == {
        "results": [
            {
                "id": "owned-result",
                "memory": "owned result",
                "event": "",
                "score": None,
                "app_id": "sample-app",
                "updated_at": "",
                "owner_id": "@sample-user:example.org",
                "attributed_to": "",
            }
        ]
    }
    fake.search_rows = [
        {
            "id": "bad.id",
            "memory": "malformed owned result",
            "agent_id": "sample-app",
            "user_id": "@sample-user:example.org",
            "metadata": {"app_id": "sample-app"},
        }
    ]
    try:
        mem0.search(
            user_id="@sample-user:example.org",
            query="sample",
            app_id="sample-app",
            limit=7,
        )
        raise AssertionError("malformed owned backend row accepted")
    except BackendContractError:
        pass


def _scope_filters(user_id: str, app_id: str) -> dict[str, str]:
    return {"user_id": user_id, "agent_id": app_id, "app_id": app_id}


def _claimed_identity(identity: str, claim: object = _UNSET) -> str:
    if claim is _UNSET:
        return identity
    if not isinstance(claim, str) or not _constant_time_text_equal(claim, identity):
        raise AuthFailure(403)
    return identity


def _memory_belongs_to(memory: Any, app_id: str) -> bool:
    row = memory if isinstance(memory, dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    agent_id = row.get("agent_id") if isinstance(row.get("agent_id"), str) else ""
    metadata_app_id = (
        metadata.get("app_id") if isinstance(metadata.get("app_id"), str) else ""
    )
    return _constant_time_text_equal(agent_id, app_id) & _constant_time_text_equal(
        metadata_app_id, app_id
    )


def _memory_user_matches(memory: Any, user_id: str) -> bool:
    row = memory if isinstance(memory, dict) else {}
    owner = row.get("user_id") if isinstance(row.get("user_id"), str) else ""
    return _constant_time_text_equal(owner, user_id)


def _mem0_results(
    data: Any,
    app_id: str,
    *,
    user_id: str = "",
    require_owner: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise BackendContractError("Mem0 response must contain a results list")
    rows = data["results"]
    if len(rows) > MAX_RESULT_ROWS:
        raise BackendContractError(
            f"Mem0 response exceeds {MAX_RESULT_ROWS} result rows"
        )
    if not IDENTITY_RE.fullmatch(app_id):
        raise BackendContractError("Mem0 result scope has an invalid app identity")

    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BackendContractError(f"Mem0 result {index} must be an object")
        if require_owner and not (
            _memory_belongs_to(row, app_id) & _memory_user_matches(row, user_id)
        ):
            continue
        out.append(_normalized_mem0_result(row, app_id, index))
    return out


def _normalized_mem0_result(
    row: dict[str, Any], app_id: str, index: int
) -> dict[str, Any]:
    memory_id = row.get("id")
    if not isinstance(memory_id, str) or not MEMORY_ID_RE.fullmatch(memory_id):
        raise BackendContractError(f"Mem0 result {index} has an invalid id")

    memory = row.get("memory")
    if (
        not isinstance(memory, str)
        or not memory.strip()
        or len(memory) > MAX_RESULT_MEMORY_CHARS
        or "\0" in memory
    ):
        raise BackendContractError(f"Mem0 result {index} has invalid memory text")

    event = row.get("event", "")
    if (
        not isinstance(event, str)
        or ("event" in row and not event)
        or len(event) > MAX_RESULT_EVENT_CHARS
        or "\0" in event
    ):
        raise BackendContractError(f"Mem0 result {index} has an invalid event")

    score = row.get("score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise BackendContractError(f"Mem0 result {index} has an invalid score")
        try:
            score = float(score)
        except OverflowError as exc:
            raise BackendContractError(
                f"Mem0 result {index} has an invalid score"
            ) from exc
        if not math.isfinite(score):
            raise BackendContractError(f"Mem0 result {index} has an invalid score")

    timestamp = ""
    for field in ("updated_at", "created_at"):
        value = row.get(field)
        if value is None:
            continue
        if (
            not isinstance(value, str)
            or not value
            or len(value) > MAX_RESULT_TIMESTAMP_CHARS
            or "\0" in value
        ):
            raise BackendContractError(
                f"Mem0 result {index} has an invalid {field} timestamp"
            )
        if not timestamp:
            timestamp = value

    metadata_value = row.get("metadata")
    if metadata_value is not None and not isinstance(metadata_value, dict):
        raise BackendContractError(f"Mem0 result {index} has invalid metadata")
    metadata = metadata_value or {}
    app_claims = [
        value
        for value in (
            row.get("agent_id"),
            row.get("app_id"),
            metadata.get("app_id"),
        )
        if value is not None
    ]
    for claim in app_claims:
        if (
            not isinstance(claim, str)
            or not IDENTITY_RE.fullmatch(claim)
            or not _constant_time_text_equal(claim, app_id)
        ):
            raise BackendContractError(
                f"Mem0 result {index} has an invalid app identity claim"
            )

    owner = row.get("user_id", "")
    # Mem0 promotes attributed_to out of metadata on get/search/list.
    source = row.get("attributed_to", "")
    if not isinstance(owner, str) or len(owner) > MAX_USER_ID_CHARS or "\0" in owner:
        raise BackendContractError(f"Mem0 result {index} has invalid owner")
    if source not in ("", "user", "assistant"):
        raise BackendContractError(f"Mem0 result {index} has invalid source")
    return {
        "id": memory_id,
        "memory": memory,
        "event": event,
        "score": score,
        "app_id": app_id,
        "updated_at": timestamp,
        "owner_id": owner,
        "attributed_to": source,
    }
def main() -> int:
    parser = argparse.ArgumentParser()
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--self-check", action="store_true")
    checks.add_argument("--api-self-check", action="store_true")
    checks.add_argument("--health-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("ok")
        return 0
    if args.api_self_check:
        asyncio.run(api_self_check())
        print("ok")
        return 0
    if args.health_check:
        runtime_health_check()
        print("ok")
        return 0
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=SERVICE_PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
