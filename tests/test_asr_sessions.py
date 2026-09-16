"""Transport races exercised with real threads/loops and a fake WebSocket."""
import asyncio
import threading
import unittest
from unittest.mock import Mock

from doubao_input.doubao.asr_client import ASRClient, MAX_PENDING_BYTES
from doubao_input.doubao.params_store import ASRParams


class FakeSocket:
    def __init__(self, block_connect=False):
        self.block_connect = block_connect
        self.entered = threading.Event()
        self.sent = threading.Event()
        self.two_sent = threading.Event()
        self.chunks = []
        self.closed = False

    async def __aenter__(self):
        self.queue = asyncio.Queue()
        self.entered.set()
        if self.block_connect:
            await asyncio.Future()
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def send(self, data):
        self.chunks.append(data)
        self.sent.set()
        if len(self.chunks) == 2:
            self.two_sent.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()


class SessionTest(unittest.TestCase):
    params = ASRParams({"session": "fake"}, "device", "web")

    def client(self, socket):
        client = ASRClient(lambda *a, **k: socket)
        self.addCleanup(client.disconnect)
        return client

    def stop(self, client, session):
        client.disconnect()
        session.thread.join(3)
        self.assertFalse(session.thread.is_alive())
        if session.loop:
            self.assertTrue(session.loop.is_closed())

    def test_modern_connection_explicitly_disables_proxy(self):
        socket = FakeSocket()
        options = {}

        def connect(url, *, additional_headers, proxy=True, **kwargs):
            options.update(proxy=proxy, headers=additional_headers)
            return socket

        client = self.client(socket)
        client._connect_factory = connect
        client.connect(self.params)
        session = client._session
        self.assertTrue(socket.entered.wait(2))
        self.stop(client, session)
        self.assertIsNone(options["proxy"])
        self.assertIn("Cookie", options["headers"])
        self.assertIn("Origin", options["headers"])

    def test_legacy_connection_does_not_receive_unsupported_proxy_option(self):
        socket = FakeSocket()
        options = {}

        def connect(url, *, extra_headers, open_timeout, close_timeout, max_size):
            options.update(headers=extra_headers)
            return socket

        client = self.client(socket)
        client._connect_factory = connect
        client.connect(self.params)
        session = client._session
        self.assertTrue(socket.entered.wait(2))
        self.stop(client, session)
        self.assertIn("Cookie", options["headers"])

    def test_cancel_during_handshake_closes_loop(self):
        socket = FakeSocket(block_connect=True)
        client = self.client(socket)
        opened = client.on_open = Mock()
        client.connect(self.params)
        session = client._session
        self.assertTrue(socket.entered.wait(2))
        self.stop(client, session)
        opened.assert_not_called()

    def test_finish_preserves_preconnection_audio_and_rejects_new_samples(self):
        socket = FakeSocket()
        client = self.client(socket)
        client.prepare()
        client.send_audio(b"first")
        client.finish_sending()
        client.finish_sending()  # Idempotent: only one silence tail.
        client.send_audio(b"rejected")
        client.connect(self.params)
        session = client._session
        self.assertTrue(socket.two_sent.wait(2))
        self.assertEqual(socket.chunks, [b"first", bytes(16000)])
        self.stop(client, session)
        self.assertTrue(socket.closed)

    def test_reconnect_rejects_old_callbacks_and_closes_both_loops(self):
        socket = FakeSocket()
        client = self.client(socket)
        results = client.on_result = Mock()
        client.connect(self.params)
        old = client._session
        self.assertTrue(socket.entered.wait(2))
        new_socket = FakeSocket()
        client._connect_factory = lambda *a, **k: new_socket
        client.connect(self.params)
        new = client._session
        self.assertTrue(new_socket.entered.wait(2))
        client._emit(old, "on_result", "stale")
        client._emit(new, "on_result", "current")
        results.assert_called_once_with("current")
        old.thread.join(3)
        self.assertFalse(old.thread.is_alive())
        self.assertTrue(old.loop.is_closed())
        self.stop(client, new)

    def test_empty_capture_and_cancel_do_not_synthesize_audio(self):
        client = self.client(FakeSocket())
        client.prepare()
        client.finish_sending()
        self.assertFalse(client.has_pending_audio)
        self.assertEqual(client.diagnostics()["audio_bytes"], 0)
        self.assertEqual(client.diagnostics()["padding_bytes"], 0)
        client.prepare()
        client.send_audio(b"captured")
        session = client._session
        client.disconnect()
        self.assertEqual(session.padding_bytes, 0)
        self.assertFalse(session.audio)

    def test_silence_tail_respects_queue_bound_and_capture_counters(self):
        client = self.client(FakeSocket())
        client.prepare()
        audio = bytes(MAX_PENDING_BYTES - 16000)
        client.send_audio(audio)
        client.finish_sending()
        self.assertEqual(client._session.pending_bytes, MAX_PENDING_BYTES)
        self.assertEqual(client.diagnostics()["audio_bytes"], len(audio))
        self.assertEqual(client.diagnostics()["padding_bytes"], 16000)

    def test_queue_is_bounded_before_connect(self):
        client = self.client(FakeSocket())
        errors = client.on_error = Mock()
        client.prepare()
        client.send_audio(b"x" * (MAX_PENDING_BYTES + 1))
        errors.assert_called_once()
        self.assertIsNone(client._session)

    def test_twenty_immediate_cancellations_leave_no_workers(self):
        client = self.client(FakeSocket(block_connect=True))
        for _ in range(20):
            client.connect(self.params)
            session = client._session
            self.stop(client, session)

    def test_empty_results_are_valid_responses_and_do_not_prevent_later_text(self):
        client = self.client(FakeSocket())
        results = client.on_result = Mock()
        client.prepare()
        session = client._session
        for message in ('{"event":"result","result":{"Text":""}}',
                        '{"event":"result","result":{"Text":" "}}'):
            self.assertFalse(client._handle_message(session, message))
        results.assert_not_called()
        self.assertEqual(client.diagnostics()["empty_results"], 2)
        self.assertEqual(client.diagnostics()["ignored"], 0)
        client._handle_message(session, '{"event":"result","result":{"Text":"words"}}')
        results.assert_called_once_with("words")
        self.assertEqual(client.diagnostics()["results"], 1)

    def test_invalid_messages_do_not_crash_or_leak_into_results(self):
        client = self.client(FakeSocket())
        results = client.on_result = Mock()
        client.prepare()
        session = client._session
        for message in ('[]', 'null', '{', '{"result": []}',
                        '{"event":"result","result":{"Text": 42}}'):
            self.assertFalse(client._handle_message(session, message))
        results.assert_not_called()
