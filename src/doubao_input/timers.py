"""Main-thread timer ownership with stale-callback rejection."""


class TimerScope:
    def __init__(self, schedule, cancel):
        self._schedule, self._cancel = schedule, cancel
        self._generation = 0
        self._handles = set()

    def later(self, milliseconds, callback):
        generation = self._generation

        def run():
            self._handles.discard(handle)
            if generation == self._generation:
                callback()
            return False

        handle = self._schedule(milliseconds, run)
        self._handles.add(handle)
        return handle

    def clear(self):
        self._generation += 1
        for handle in self._handles:
            self._cancel(handle)
        self._handles.clear()
