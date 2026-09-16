"""Session-owned asynchronous Doubao transport; never touches GTK.

Each recording owns its loop, task, bounded audio queue and callback snapshot.
Disconnect invalidates the owner immediately; cleanup runs on the worker.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import inspect
import json
import logging
import threading
import time
import uuid
from urllib.parse import urlencode

from doubao_input.doubao.config import (
    AUDIO_CHANNELS, AUDIO_SAMPLE_RATE, AUTH_ERROR_CODE, AUTH_ERROR_KEYWORDS,
    FIXED_QUERY_PARAMS, ORIGIN, WSS_BASE_URL,
)
from doubao_input.doubao.params_store import ASRParams

logger = logging.getLogger(__name__)
MAX_PENDING_BYTES = 1024 * 1024
# 500 ms of s16 PCM lets the service resolve the final word of short utterances.
# Merely waiting with no more audio reproducibly leaves "Front center" as "Front".
END_SILENCE = bytes(AUDIO_SAMPLE_RATE * AUDIO_CHANNELS)


@dataclass(eq=False)
class _Session:
    started: float = field(default_factory=time.monotonic)
    trace: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    received_bytes: int = 0
    padding_bytes: int = 0
    sent_bytes: int = 0
    messages: int = 0
    results: int = 0
    empty_results: int = 0
    ignored: int = 0
    drained_at: float | None = None
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

    @property
    def drained_for(self) -> float:
        with self._lock:
            when = self._session.drained_at if self._session else None
            return time.monotonic() - when if when is not None else 0.0

    def diagnostics(self) -> dict:
        """Counters only: no account, audio, transcript or connection URL."""
        with self._lock:
            s = self._session
            if s is None:
                return {}
            return dict(trace=s.trace, elapsed_ms=round((time.monotonic() - s.started) * 1000),
                        audio_bytes=s.received_bytes, padding_bytes=s.padding_bytes, sent_bytes=s.sent_bytes,
                        pending_bytes=s.pending_bytes, sending=s.sending,
                        connected=s.connected, messages=s.messages, results=s.results,
                        empty_results=s.empty_results, ignored=s.ignored)

    def prepare(self) -> Callable[[bytes], None]:
        """Begin a recording and return a PCM callback bound to this session."""
        self.disconnect()
        with self._lock:
            self._session = _Session()
            self._session.callbacks = {name: getattr(self, name) for name in (
                "on_open", "on_result", "on_finish", "on_error", "on_auth_error")}
            owner = self._session
            return lambda data: self.send_audio(data, _owner=owner)

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
            logger.info("ASR %s connecting directly", session.trace)
            factory = self._connect_factory or _load_websockets().connect
            headers = {"Cookie": params.cookie_header, "Origin": ORIGIN}
            async with factory(self._build_url(params), open_timeout=5, close_timeout=3,
                               max_size=2**20, **_websocket_connect_kwargs(factory, headers)) as ws:
                with self._lock:
                    if session.cancelled:
                        return
                    session.connected = True
                logger.info("ASR %s connected after %.0f ms", session.trace,
                            (time.monotonic() - session.started) * 1000)
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
                    if chunk is None and not session.accepting and session.drained_at is None:
                        session.drained_at = time.monotonic()
                        logger.info("ASR %s audio drained: bytes=%d", session.trace, session.sent_bytes)
                if chunk is None:
                    break
                try:
                    await ws.send(chunk)
                    with self._lock:
                        first = session.sent_bytes == 0
                        session.sent_bytes += len(chunk)
                    if first:
                        logger.info("ASR %s first audio sent after %.0f ms", session.trace,
                                    (time.monotonic() - session.started) * 1000)
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

    def send_audio(self, data: bytes, *, _owner: _Session | None = None) -> None:
        with self._lock:
            if _owner is not None and self._session is not _owner:
                return
            session = self._session
            if session is None or session.cancelled or not session.accepting:
                return
            if session.pending_bytes + len(data) > MAX_PENDING_BYTES - len(END_SILENCE):
                self._emit(session, "on_error", RuntimeError("ASR audio queue is full"))
                self.disconnect()
                return
            session.audio.append(bytes(data))
            session.received_bytes += len(data)
            session.pending_bytes += len(data)
            if session.loop and session.wake and not session.loop.is_closed():
                try:
                    session.loop.call_soon_threadsafe(session.wake.set)
                except RuntimeError:
                    pass  # Loop completed between the checks.

    def finish_sending(self) -> None:
        """Stop accepting audio, retaining queued samples and final responses."""
        with self._lock:
            if self._session and self._session.accepting:
                session = self._session
                session.accepting = False
                if session.received_bytes:
                    session.audio.append(END_SILENCE)
                    session.pending_bytes += len(END_SILENCE)
                    session.padding_bytes = len(END_SILENCE)
                if session.loop and session.wake and not session.loop.is_closed():
                    try:
                        session.loop.call_soon_threadsafe(session.wake.set)
                    except RuntimeError:
                        pass

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
        with self._lock:
            session.messages += 1
        try:
            data = json.loads(message)
        except (ValueError, TypeError, UnicodeError):
            with self._lock:
                session.ignored += 1
            return False
        if not isinstance(data, dict):
            with self._lock:
                session.ignored += 1
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
            if isinstance(text, str) and not text.strip():
                with self._lock:
                    session.empty_results += 1
                return False
            if isinstance(text, str) and text:
                with self._lock:
                    session.results += 1
                    first = session.results == 1
                if first:
                    logger.info("ASR %s first result after %.0f ms; characters=%d", session.trace,
                                (time.monotonic() - session.started) * 1000, len(text))
                self._emit(session, "on_result", text)
                return False
        elif event == "finish":
            logger.info("ASR %s server finish", session.trace)
            self._emit(session, "on_finish")
            return True
        with self._lock:
            session.ignored += 1
        return False


def _load_websockets():
    try:
        import websockets
    except ImportError as error:
        raise RuntimeError("Install the Python websockets package before recording") from error
    return websockets


def _websocket_connect_kwargs(connect_func, headers: dict[str, str]) -> dict:
    """Connect directly; older websockets versions have no automatic proxy support."""
    try:
        params = inspect.signature(connect_func).parameters
    except (TypeError, ValueError):
        return {"additional_headers": headers, "proxy": None}
    kwargs = {"additional_headers" if "additional_headers" in params else "extra_headers": headers}
    if "proxy" in params:
        kwargs["proxy"] = None
    return kwargs
