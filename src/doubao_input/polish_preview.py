"""One recording's speculative polishing cache and silence timer."""


class PolishPreview:
    def __init__(self, schedule, remove):
        self._schedule, self._remove = schedule, remove
        self._timer = None
        self.snapshot = ""
        self.result = None
        self.inflight = ""

    def arm(self, callback):
        self.cancel_timer()
        def fire():
            self._timer = None
            callback()
            return False
        self._timer = self._schedule(1200, fire)

    def cancel_timer(self):
        if self._timer is not None:
            self._remove(self._timer)
            self._timer = None

    def invalidate(self, text):
        if text != self.snapshot:
            self.result = None
        return bool(self.inflight and self.inflight != text)

    def begin(self, text):
        self.snapshot = self.inflight = text
        self.result = None

    def finish(self, text, result):
        self.inflight = ""
        self.snapshot, self.result = text, result

    def cached(self, text):
        return self.result if text == self.snapshot else None

    def reset(self):
        self.cancel_timer()
        self.snapshot = self.inflight = ""
        self.result = None
