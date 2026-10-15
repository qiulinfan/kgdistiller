"""The user's one embedding process.

Every kgdistiller process that needs vectors, ``kgd index``, ``kgd accept``,
``kgd harvest`` and the dense lane of every ``kgd search`` and ``kgd mcp``,
asks this process instead of loading the model itself, so at most one model is
in memory per user, whatever the number of Obsidian windows and agent
sessions. The first request starts it; it loads the model on its first request
for that model and answers one request at a time; it exits 15 minutes after
its last request, giving all its memory back. Embedding often comes in runs
while the owner edits knowledge, so a run pays the cold start once.

It only encodes: texts in, vectors out. It never opens the database, so
``kgd index`` stays the database's only writer.

Its socket, or named pipe on Windows, its key, its lock and its log live in a
private directory under the user's temporary directory. The process holds the
lock for its whole life, so a second one exits at once and a socket file with
no lock holder is left over from a process that died. Each request is one
authenticated connection, so a long run of batches never blocks another
process for more than one batch.
"""

from __future__ import annotations

import gc
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .embedding import EmbeddingError, RetrievalExtraMissing

IDLE_SECONDS = 15 * 60
START_SECONDS = 60
REPLY_SECONDS = 30 * 60
BACKLOG = 64
RESTART_SECONDS = 1.0
KEY_FILENAME = "embed.key"
LOCK_FILENAME = "embed.lock"
LOG_FILENAME = "embed.log"
_started: list[subprocess.Popen[bytes]] = []


def runtime_directory() -> Path:
    """The private directory of the user's embedding process."""
    owner = getattr(os, "getuid", None)
    path = Path(tempfile.gettempdir()) / (f"kgdistiller-{owner()}" if owner else "kgdistiller")
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or (owner and path.stat().st_uid != owner()):
        raise EmbeddingError(f"{path} must be a directory of this user, not a symlink")
    return path


def address(directory: Path) -> str:
    if os.name == "nt":
        return r"\\.\pipe\kgdistiller-embed-" + str(directory).replace("\\", "-").replace(":", "")
    return str(directory / "embed.sock")


def authkey(directory: Path) -> bytes:
    """The key every connection proves; created once, readable by this user only."""
    path = directory / KEY_FILENAME
    if not path.exists():
        descriptor, temporary = tempfile.mkstemp(dir=directory)
        try:
            os.write(descriptor, secrets.token_bytes(32))
            os.close(descriptor)
            os.link(temporary, path)
        except FileExistsError:
            pass
        finally:
            os.unlink(temporary)
    return path.read_bytes()


def _hold(directory: Path) -> Any:
    """The process lock, held until exit, or None when another process holds it."""
    handle = (directory / LOCK_FILENAME).open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")  # msvcrt locks bytes, so the file needs one
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _answer(request: Any, encoder: Any, load: Any) -> tuple[tuple[str, Any], Any]:
    operation, model, payload = request
    try:
        if encoder is None or encoder.model != model:
            encoder = None
            gc.collect()
            encoder = load(model)
        if operation == "documents":
            return ("ok", encoder.encode_documents(payload)), encoder
        if operation == "query":
            return ("ok", encoder.encode_query(payload)), encoder
        if operation == "over_limit":
            return ("ok", encoder.over_limit(payload)), encoder
        return ("error", f"unknown embedding request {operation!r}"), encoder
    except RetrievalExtraMissing as error:
        return ("missing", str(error)), encoder
    except EmbeddingError as error:
        return ("error", str(error)), encoder
    except Exception as error:  # noqa: BLE001 - every failure is the client's answer; the process keeps serving
        return ("error", f"embedding with {model} failed: {error}"), encoder


def serve(directory: Path, idle: float = IDLE_SECONDS, load: Any = None) -> None:
    """Answer embedding requests at ``directory`` until ``idle`` seconds pass without one."""
    from multiprocessing.connection import AuthenticationError, Listener

    if load is None:
        from .embedding import Encoder as load
    held = _hold(directory)
    if held is None:
        return  # another process answers here
    key = authkey(directory)
    where = address(directory)
    if os.name != "nt":
        Path(where).unlink(missing_ok=True)  # left by a process that died; the lock says none is alive
    listener = Listener(where, backlog=BACKLOG, authkey=key)
    guard = threading.Lock()
    state = {"last": time.monotonic(), "busy": False}

    def watch() -> None:
        while True:
            time.sleep(min(idle, 5.0))
            with guard:
                if not state["busy"] and time.monotonic() - state["last"] >= idle:
                    if os.name != "nt":
                        Path(where).unlink(missing_ok=True)
                    os._exit(0)  # releases the lock

    threading.Thread(target=watch, daemon=True).start()
    encoder = None
    while True:
        try:
            connection = listener.accept()
        except (OSError, EOFError, AuthenticationError):
            continue
        with guard:
            state["busy"] = True
        try:
            reply, encoder = _answer(connection.recv(), encoder, load)
            connection.send(reply)
        except (OSError, EOFError):
            pass
        finally:
            connection.close()
            with guard:
                state["busy"] = False
                state["last"] = time.monotonic()


def _start(directory: Path) -> None:
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                    | subprocess.CREATE_NO_WINDOW)
    else:
        options["start_new_session"] = True
    with (directory / LOG_FILENAME).open("ab") as log:
        _started.append(subprocess.Popen(
            [sys.executable, "-m", "kgdistiller.adapters.embed_service", str(directory)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, close_fds=True, **options,
        ))


class Client:
    """The ``Encoder`` interface, answered by the user's embedding process."""

    def __init__(self, model: str, directory: Path | None = None) -> None:
        self.model = model
        self._directory = directory

    def over_limit(self, texts: list[str]) -> list[int]:
        return self._call("over_limit", texts)

    def encode_documents(self, texts: list[str]) -> list[bytes]:
        return self._call("documents", texts)

    def encode_query(self, text: str) -> bytes:
        return self._call("query", text)

    def _call(self, operation: str, payload: Any) -> Any:
        directory = self._directory or runtime_directory()
        for attempt in range(2):
            connection = self._connect(directory)
            try:
                connection.send((operation, self.model, payload))
                if not connection.poll(REPLY_SECONDS):
                    raise EmbeddingError(f"the embedding process gave no answer in {REPLY_SECONDS} s")
                status, value = connection.recv()
            except (OSError, EOFError) as error:
                # It exited between accepting and answering, or was stopped: start another once.
                if attempt == 0:
                    continue
                raise EmbeddingError(f"the embedding process stopped; see {directory / LOG_FILENAME}") from error
            finally:
                connection.close()
            if status == "ok":
                return value
            if status == "missing":
                raise RetrievalExtraMissing(value)
            raise EmbeddingError(value)
        raise AssertionError("unreachable")

    def _connect(self, directory: Path) -> Any:
        from multiprocessing.connection import AuthenticationError
        from multiprocessing.connection import Client as connect

        key = authkey(directory)
        where = address(directory)
        deadline = time.monotonic() + START_SECONDS
        started = 0.0
        while True:
            try:
                return connect(where, authkey=key)
            except AuthenticationError as error:
                raise EmbeddingError(f"the embedding process at {where} refused this user's key") from error
            except OSError as error:
                now = time.monotonic()
                if now > deadline:
                    raise EmbeddingError(
                        f"the embedding process did not start in {START_SECONDS} s; see {directory / LOG_FILENAME}"
                    ) from error
                # A process that is exiting holds the lock a moment longer, so a start can be a no-op: start again.
                if now - started >= RESTART_SECONDS:
                    _start(directory)
                    started = now
                time.sleep(0.05)

if __name__ == "__main__":
    serve(Path(sys.argv[1]))
