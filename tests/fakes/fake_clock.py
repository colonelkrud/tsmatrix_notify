class FakeClock:
    def __init__(self, now: float = 0.0):
        self._now = float(now)

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)
