"""Injectable millisecond clocks for process-local action deadlines."""

import time
from typing import Protocol


class Clock(Protocol):
    def now_monotonic_ms(self) -> int: ...

    def now_unix_ms(self) -> int: ...


class SystemClock:
    def now_monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def now_unix_ms(self) -> int:
        return time.time_ns() // 1_000_000
