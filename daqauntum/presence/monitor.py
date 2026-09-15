from __future__ import annotations

import threading


class PresenceMonitorThread:
    """Passive environmental sampler. It never initiates radio discovery."""

    def __init__(self, manager, interval_seconds: int = 20):
        self.manager = manager
        self.interval = max(5, int(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="daqauntum-presence", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                self.manager.passive_snapshot(save=True)
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
