import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.errors import ServicePausedError, ValidationError
from app.domain.providers import ProviderId

RUNS_SWITCH = "runs"
PROVIDER_PREFIX = "provider:"
CACHE_SECONDS = 5.0


class SwitchStore(Protocol):
    def list_all(self) -> list[dict[str, Any]]: ...

    def set(self, switch_id: str, paused: bool, reason: str) -> dict[str, Any]: ...

    def history(self, limit: int) -> list[dict[str, Any]]: ...


def validate_switch_id(switch_id: str) -> str:
    if switch_id == RUNS_SWITCH:
        return switch_id
    if switch_id.startswith(PROVIDER_PREFIX) and switch_id.removeprefix(PROVIDER_PREFIX) in ProviderId._value2member_map_:
        return switch_id
    raise ValidationError(f"Unknown switch {switch_id!r}; use 'runs' or 'provider:<provider id>'")


@dataclass(frozen=True, slots=True)
class SwitchState:
    paused: dict[str, str]

    def paused_providers(self) -> frozenset[ProviderId]:
        return frozenset(ProviderId(s.removeprefix(PROVIDER_PREFIX)) for s in self.paused if s.startswith(PROVIDER_PREFIX))


class OpsSwitches:
    def __init__(self, store: SwitchStore, clock: Callable[[], float] = time.monotonic) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: tuple[float, SwitchState] | None = None

    def state(self) -> SwitchState:
        with self._lock:
            if self._cached and self._clock() - self._cached[0] < CACHE_SECONDS:
                return self._cached[1]
        state = SwitchState({s["switch_id"]: s["reason"] for s in self._store.list_all() if s["paused"]})
        with self._lock:
            self._cached = (self._clock(), state)
        return state

    def set(self, switch_id: str, paused: bool, reason: str) -> dict[str, Any]:
        changed = self._store.set(validate_switch_id(switch_id), paused, reason)
        with self._lock:
            self._cached = None
        return changed

    def list_all(self) -> list[dict[str, Any]]:
        return self._store.list_all()

    def history(self, limit: int) -> list[dict[str, Any]]:
        return self._store.history(limit)

    def paused_providers(self) -> frozenset[ProviderId]:
        return self.state().paused_providers()

    def ensure_runs_allowed(self) -> None:
        reason = self.state().paused.get(RUNS_SWITCH)
        if reason is not None:
            raise ServicePausedError(
                f"New questions are paused by an operator: {reason}" if reason else "New questions are paused by an operator.",
                remediation="Try again later. Runs already in progress continue.",
            )
