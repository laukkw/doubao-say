"""Single input worker; completions are dispatched to the GTK main thread."""
from concurrent.futures import ThreadPoolExecutor


class InputWorker:
    def __init__(self, dispatch):
        self._dispatch = dispatch
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="doubao-say")
        self._closed = False

    def submit(self, work, completed):
        if self._closed:
            raise RuntimeError("Input worker is closed")
        future = self._executor.submit(work)

        def finished(future):
            try:
                result = future.result()
            except Exception:
                result = "failed"

            def deliver():
                if not self._closed:
                    completed(result)
                return False

            self._dispatch(deliver)

        future.add_done_callback(finished)

    def close(self, cleanup):
        if not self._closed:
            self._closed = True
            # Release the virtual device after cancelled work drains, never while
            # another task holds its lock. Do not block the GTK shutdown callback.
            self._executor.submit(cleanup)
            self._executor.shutdown(wait=False)
