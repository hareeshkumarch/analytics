from decimal import Decimal
import threading
import uuid

from app.domain.errors import BudgetExhaustedError
from app.domain.limits import DEEP_BUDGET_LIMITS
from app.domain.providers import BudgetReservation

DEFAULT_MAX_ATTEMPTS = DEEP_BUDGET_LIMITS.total_model_attempts
DEFAULT_MAX_TOKENS = 500_000


class AtomicBudgetLedger:
    def __init__(
        self,
        *,
        max_attempts_per_run: dict[str, int] | None = None,
        max_tokens_per_run: dict[str, int] | None = None,
        max_cost_usd_per_run: dict[str, Decimal] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._max_attempts = max_attempts_per_run or {}
        self._max_tokens = max_tokens_per_run or {}
        self._max_cost = max_cost_usd_per_run or {}
        self._used_attempts: dict[str, int] = {}
        self._reserved_tokens: dict[str, int] = {}
        self._reserved_cost: dict[str, Decimal] = {}
        self._reservations: dict[str, BudgetReservation] = {}
        self._has_unresolved: dict[str, bool] = {}

    def configure_run(
        self,
        run_id: str,
        *,
        max_attempts: int,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_cost_usd: Decimal | None = None,
    ) -> None:
        with self._lock:
            self._max_attempts[run_id] = max_attempts
            self._max_tokens[run_id] = max_tokens
            if max_cost_usd is not None:
                self._max_cost[run_id] = max_cost_usd

    def reserve(
        self,
        run_id: str,
        operation_id: str,
        attempt_id: str,
        estimated_tokens: int,
        estimated_cost_usd: Decimal | None,
        cost_is_unknown: bool,
        hard_cost_ceiling_usd: Decimal | None,
    ) -> BudgetReservation:
        with self._lock:
            ceiling = hard_cost_ceiling_usd if hard_cost_ceiling_usd is not None else self._max_cost.get(run_id)
            if ceiling is not None and cost_is_unknown:
                raise BudgetExhaustedError(
                    "Hard monetary ceiling excludes models with unknown pricing",
                    remediation="Select a model with declared pricing or remove the monetary ceiling.",
                )

            used_att = self._used_attempts.get(run_id, 0)
            max_att = self._max_attempts.get(run_id, DEFAULT_MAX_ATTEMPTS)
            if used_att + 1 > max_att:
                raise BudgetExhaustedError(f"Run {run_id} exceeded max model attempts ({max_att})")

            cur_tok = self._reserved_tokens.get(run_id, 0)
            max_tok = self._max_tokens.get(run_id, DEFAULT_MAX_TOKENS)
            if cur_tok + estimated_tokens > max_tok:
                raise BudgetExhaustedError(f"Run {run_id} exceeded token budget ({max_tok})")

            cur_cost = self._reserved_cost.get(run_id, Decimal("0"))
            add_cost = estimated_cost_usd or Decimal("0")
            if ceiling is not None and cur_cost + add_cost > ceiling:
                raise BudgetExhaustedError(
                    f"Run {run_id} exceeded monetary ceiling ({ceiling} USD)"
                )

            self._used_attempts[run_id] = used_att + 1
            self._reserved_tokens[run_id] = cur_tok + estimated_tokens
            self._reserved_cost[run_id] = cur_cost + add_cost

            res_id = f"res_{uuid.uuid4().hex[:12]}"
            res = BudgetReservation(
                reservation_id=res_id,
                run_id=run_id,
                operation_id=operation_id,
                attempt_id=attempt_id,
                reserved_tokens=estimated_tokens,
                reserved_cost_usd=estimated_cost_usd,
                cost_is_unknown=cost_is_unknown,
                settled=False,
            )
            self._reservations[res_id] = res
            return res

    def settle(
        self,
        reservation_id: str,
        actual_tokens: int,
        actual_cost_usd: Decimal | None,
    ) -> BudgetReservation:
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res is None:
                raise KeyError(f"Unknown reservation_id: {reservation_id}")
            if res.settled:
                return res

            token_delta = actual_tokens - res.reserved_tokens
            self._reserved_tokens[res.run_id] = max(0, self._reserved_tokens.get(res.run_id, 0) + token_delta)

            if actual_cost_usd is not None and res.reserved_cost_usd is not None:
                cost_delta = actual_cost_usd - res.reserved_cost_usd
                self._reserved_cost[res.run_id] = max(
                    Decimal("0"), self._reserved_cost.get(res.run_id, Decimal("0")) + cost_delta
                )

            settled = BudgetReservation(
                reservation_id=res.reservation_id,
                run_id=res.run_id,
                operation_id=res.operation_id,
                attempt_id=res.attempt_id,
                reserved_tokens=res.reserved_tokens,
                reserved_cost_usd=res.reserved_cost_usd,
                cost_is_unknown=res.cost_is_unknown,
                settled=True,
                actual_tokens=actual_tokens,
                actual_cost_usd=actual_cost_usd,
            )
            self._reservations[reservation_id] = settled
            return settled

    def release_unresolved(self, reservation_id: str, reason: str) -> None:
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res is not None and not res.settled:
                self._has_unresolved[res.run_id] = True

    def get_runs_totals(self, run_ids: list[str]) -> dict[str, dict[str, object]]:
        return {run_id: self.get_run_totals(run_id) for run_id in run_ids}

    def get_run_totals(self, run_id: str) -> dict[str, object]:
        with self._lock:
            return {
                "run_id": run_id,
                "used_attempts": self._used_attempts.get(run_id, 0),
                "total_tokens": self._reserved_tokens.get(run_id, 0),
                "total_cost_usd": str(self._reserved_cost.get(run_id, Decimal("0"))),
                "incomplete": self._has_unresolved.get(run_id, False),
            }
