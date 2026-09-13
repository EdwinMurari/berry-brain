"""Local storage adapter. Uses the service's engine without HTTP or model calls."""

import os
import re
import stat
import sys
from pathlib import Path

from pydantic import ValidationError

from .engine import Brain, BrainError


def default_directory() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        configured = Path(os.environ.get("XDG_DATA_HOME", ""))
        base = configured if configured.is_absolute() else Path.home() / ".local" / "share"
    return base / "berry-brain"


def private_database(directory: Path) -> Path:
    directory = Path(directory).expanduser().absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = directory.lstat()
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError("brain data directory must be a real directory, not a link")
    if os.name != "nt" and (details.st_uid != os.getuid() or details.st_mode & 0o077):
        raise ValueError("brain data directory must belong to you and have mode 0700")
    path = directory / "brain.sqlite3"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            details = candidate.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValueError("brain database files must be regular files without links")
        if os.name != "nt" and (details.st_uid != os.getuid() or details.st_mode & 0o077):
            raise ValueError("brain database files must belong to you and have mode 0600")
    return path


class LocalClient:
    def __init__(self, directory: Path, identity: str, projects: list[str]):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", identity):
            raise ValueError("use a client name with lowercase letters, digits, dots, dashes or underscores")
        if not projects or any(not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", p) for p in projects):
            raise ValueError("provide at least one project name using lowercase letters, digits, dots, dashes or underscores")
        self.identity = identity
        policy = {"clients": {identity: {"projects": {p: {"write": True} for p in projects}}}}
        self.brain = Brain(private_database(directory), policy)

    def request(self, path, data=None):
        try:
            if path == "tools" and data is None:
                return self.brain.catalogue(self.identity)
            return self.brain.call(path, self.identity, data)
        except BrainError as exc:
            raise RuntimeError(f"brain {exc.status}: {exc}") from None
        except ValidationError as exc:
            # Do not return the input payload as part of a validation failure.
            raise RuntimeError("invalid brain arguments: " + "; ".join(
                ".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors())) from None
