"""Configurable HTTP adapter for the TypeSafe-compatible Choice API."""

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .client import NoRedirect, read_token_file
from .engine import encode
from .selection import ChoiceAnswer, ChoiceQuestion, LessonSelector, MAX_INPUT_BYTES


MAX_RESPONSE_BYTES = 1024 * 1024


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    url: str
    model: str = Field(min_length=1, max_length=200)
    token_file: str | None = None
    token_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    response_model: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("url")
    @classmethod
    def endpoint(cls, value: str) -> str:
        parsed = urllib.parse.urlsplit(value)
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)
                or not (parsed.scheme == "https" or
                        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("evaluation URL must use HTTPS or loopback HTTP without credentials or query parameters")
        return value

    @model_validator(mode="after")
    def credentials(self):
        if bool(self.token_file) == bool(self.token_env):
            raise ValueError("set exactly one of token_file or token_env")
        return self


class HttpEvaluator:
    def __init__(self, config: EvaluationConfig):
        self.config = config
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        # Check credentials at setup without making a model call.
        self._token()

    def _token(self) -> str:
        try:
            token = (read_token_file(Path(self.config.token_file).expanduser()) if self.config.token_file
                     else os.environ.get(self.config.token_env or "", "").strip())
        except (OSError, ValueError):
            raise ValueError("evaluation token file must be readable and private") from None
        if not token or not all(33 <= ord(char) <= 126 for char in token):
            raise ValueError("evaluation credential is missing or invalid")
        return token

    def evaluate(self, state: dict, questions: dict[str, ChoiceQuestion]) -> dict[str, ChoiceAnswer]:
        payload = encode({"model": self.config.model, "state": state, "questions": questions}).encode()
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError("selection request exceeded limit")
        request = urllib.request.Request(self.config.url, data=payload, headers={
            "Authorization": "Bearer " + self._token(), "Content-Type": "application/json"})
        try:
            with self.http.open(request, timeout=5) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("evaluation response exceeded limit")
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ValueError(f"evaluation HTTP {exc.code}") from None
        except (OSError, urllib.error.URLError, http.client.HTTPException,
                json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("evaluation request failed") from None
        if not isinstance(result, dict) or result.get("model") != (self.config.response_model or self.config.model):
            raise ValueError("evaluation response model differs")
        usage = result.get("usage")
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                              for k in ("input_tokens", "output_tokens")):
            raise ValueError("evaluation response has invalid usage")
        if not isinstance(result.get("answers"), dict):
            raise ValueError("evaluation response has no answers")
        return result["answers"]


def configured_selector(path: Path) -> LessonSelector:
    """Load a user-owned config. Never include its content in an error."""
    try:
        path = path.expanduser().absolute()
        if path.stat().st_size > 16384:
            raise ValueError("oversized config")
        config = EvaluationConfig.model_validate_json(path.read_text(encoding="utf-8"))
        if config.token_file:
            token_file = Path(config.token_file).expanduser()
            if not token_file.is_absolute():
                config = config.model_copy(update={"token_file": str(path.parent / token_file)})
    except (OSError, ValueError):
        raise ValueError("invalid evaluation config; check URL, model and credential reference") from None
    return LessonSelector(HttpEvaluator(config))
