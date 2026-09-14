"""Session-owned asynchronous Doubao transport; never touches GTK.

Each recording owns its loop, task, bounded audio queue and callback snapshot.
Disconnect invalidates the owner immediately; cleanup runs on the worker.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import inspect
import json
import logging
import threading
import uuid
from urllib.parse import urlencode

from doubao_input.doubao.config import (
    AUTH_ERROR_CODE, AUTH_ERROR_KEYWORDS, FIXED_QUERY_PARAMS, ORIGIN, WSS_BASE_URL,
)
from doubao_input.doubao.params_store import ASRParams

logger = logging.getLogger(__name__)
MAX_PENDING_BYTES = 1024 * 1024


@dataclass(eq=False)
class _Session:
    audio: deque = field(default_factory=deque)
    pending_bytes: int = 0
    sending: bool = False
    accepting: bool = True
    connected: bool = False
    cancelled: bool = False
    callbacks: dict = field(default_factory=dict)
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task | None = None
    wake: asyncio.Event | None = None
    thread: threading.Thread | None = None


class ASRClient:
    """Public methods are thread-safe; callbacks run on the transport thread."""

    def __init__(self, connect_factory=None) -> None:
        self._lock = threading.RLock()
        self._session: _Session | None = None
        self._connect_factory = connect_factory
        self.on_open = self.on_result = self.on_finish = None
        self.on_error = self.on_auth_error = None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return bool(self._session and self._session.connected)

    @property
    def has_pending_audio(self) -> bool:
        """Include the in-flight chunk, not just the waiting queue."""
        with self._lock:
            return bool(self._session and (self._session.audio or self._session.sending))

    def prepare(self) -> None:
        """Begin buffering a new recording before microphone capture starts."""
        self.disconnect()
        with self._lock:
            self._session = _Session()
            self._session.callbacks = {name: getattr(self, name) for name in (
                "on_open", "on_result", "on_finish", "on_error", "on_auth_error")}

    def connect(self, params: ASRParams) -> None:
        params.validate()
        with self._lock:
            if self._session is None or self._session.thread is not None:
                self.prepare()
            session = self._session
            session.callbacks = {name: getattr(self, name) for name in (
                "on_open", "on_result", "on_finish", "on_error", "on_auth_error")}
            session.thread = threading.Thread(
                target=self._run, args=(session, params), name="doubao-asr", daemon=True)
            session.thread.start()

    def _emit(self, session, name, *args):
        with self._lock:
            if self._session is session and not session.cancelled:
                callback = session.callbacks.get(name)
                if callback:
                    callback(*args)

    def _run(self, session, params):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with self._lock:
                if session.cancelled:
                    return
                session.loop = loop
                session.wake = asyncio.Event()
                session.task = loop.create_task(self._listen(session, params))
            loop.run_until_complete(session.task)
        except asyncio.CancelledError:
            pass
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            with self._lock:
                session.connected = False
                session.accepting = False
                session.audio.clear()
                session.pending_bytes = 0

    async def _listen(self, session, params):
        tasks = []
        try:
            factory = self._connect_factory or _load_websockets().connect
            headers = {"Cookie": params.cookie_header, "Origin": ORIGIN}
            async with factory(self._build_url(params), open_timeout=5, close_timeout=3,
                               max_size=2**20, **_websocket_header_kwargs(factory, headers)) as ws:
                with self._lock:
                    if session.cancelled:
                        return
                    session.connected = True
                self._emit(session, "on_open")
                tasks = [asyncio.create_task(self._send(session, ws)),
                         asyncio.create_task(self._receive(session, ws))]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Do not log exception contents: network libraries can include URLs.
            logger.warning("ASR transport failed (%s)", type(error).__name__)
            self._emit(session, "on_error", RuntimeError("ASR connection failed"))
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            with self._lock:
                session.connected = False

    async def _send(self, session, ws):
        while True:
            session.wake.clear()
            while True:
                with self._lock:
                    if session.cancelled:
                        return
                    chunk = session.audio.popleft() if session.audio else None
                    if chunk is not None:
                        session.pending_bytes -= len(chunk)
                        session.sending = True
                if chunk is None:
                    break
                try:
                    await ws.send(chunk)
                finally:
                    with self._lock:
                        session.sending = False
            await session.wake.wait()

    async def _receive(self, session, ws):
        async for message in ws:
            if self._handle_message(session, message):
                return
        with self._lock:
            finished = not session.accepting
        if finished:
            self._emit(session, "on_finish")
        else:
            raise ConnectionError("ASR closed before recording ended")

    def send_audio(self, data: bytes) -> None:
        with self._lock:
            session = self._session
            if session is None or session.cancelled or not session.accepting:
                return
            if session.pending_bytes + len(data) > MAX_PENDING_BYTES:
                self._emit(session, "on_error", RuntimeError("ASR audio queue is full"))
                self.disconnect()
                return
            session.audio.append(bytes(data))
            session.pending_bytes += len(data)
            if session.loop and session.wake and not session.loop.is_closed():
                try:
                    session.loop.call_soon_threadsafe(session.wake.set)
                except RuntimeError:
                    pass  # Loop completed between the checks.

    def finish_sending(self) -> None:
        """Stop accepting audio, retaining queued samples and final responses."""
        with self._lock:
            if self._session:
                self._session.accepting = False

    def disconnect(self) -> None:
        """Invalidate callbacks synchronously; cancel connection/IO asynchronously."""
        with self._lock:
            session, self._session = self._session, None
            if session is None:
                return
            session.cancelled = True
            session.connected = False
            session.audio.clear()
            session.pending_bytes = 0
            if session.loop and session.task:
                try:
                    session.loop.call_soon_threadsafe(session.task.cancel)
                except RuntimeError:
                    pass  # Already closed; no worker remains to cancel.

    def _build_url(self, params: ASRParams) -> str:
        query = dict(FIXED_QUERY_PARAMS, device_id=params.device_id,
                     web_id=params.web_id, tea_uuid=params.web_id,
                     web_tab_id=str(uuid.uuid4()))
        return f"{WSS_BASE_URL}?{urlencode(query)}"

    def _handle_message(self, session, message) -> bool:
        try:
            data = json.loads(message)
        except (ValueError, TypeError, UnicodeError):
            return False
        if not isinstance(data, dict):
            return False
        code, event = data.get("code", 0), data.get("event", "")
        message = data.get("message", "")
        if code != 0:
            auth = code == AUTH_ERROR_CODE or (isinstance(message, str) and any(
                word in message.lower() for word in AUTH_ERROR_KEYWORDS))
            self._emit(session, "on_auth_error" if auth else "on_error",
                       *(() if auth else (RuntimeError("ASR service rejected the request"),)))
            return True
        if event == "result":
            result = data.get("result")
            text = result.get("Text") if isinstance(result, dict) else None
            if isinstance(text, str) and text:
                self._emit(session, "on_result", text)
        elif event == "finish":
            self._emit(session, "on_finish")
            return True
        return False


def _load_websockets():
    try:
        import websockets
    except ImportError as error:
        raise RuntimeError("Install the Python websockets package before recording") from error
    return websockets


def _websocket_header_kwargs(connect_func, headers: dict[str, str]) -> dict:
    try:
        params = inspect.signature(connect_func).parameters
    except (TypeError, ValueError):
        return {"additional_headers": headers}
    return {"additional_headers" if "additional_headers" in params else "extra_headers": headers}
