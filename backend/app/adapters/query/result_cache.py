import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")

DEFAULT_MAX_ENTRIES = 256


@dataclass
class _Flight(Generic[T]):
    done: threading.Event = field(default_factory=threading.Event)
    value: T | None = None
    error: BaseException | None = None


class SingleFlightCache(Generic[T]):
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[Hashable, T] = OrderedDict()
        self._flights: dict[Hashable, _Flight[T]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: Hashable, compute: Callable[[], T]) -> tuple[T, bool]:
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key], True
            flight = self._flights.get(key)
            leader = flight is None
            if flight is None:
                flight = self._flights[key] = _Flight()
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value, True  # type: ignore[return-value]
        try:
            flight.value = compute()
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)
                if flight.error is None:
                    self._entries[key] = flight.value  # type: ignore[assignment]
                    while len(self._entries) > self._max_entries:
                        self._entries.popitem(last=False)
            flight.done.set()
        return flight.value, False

    def invalidate(self, predicate: Callable[[Hashable], bool]) -> int:
        with self._lock:
            stale = [key for key in self._entries if predicate(key)]
            for key in stale:
                del self._entries[key]
        return len(stale)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
