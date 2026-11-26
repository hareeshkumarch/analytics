from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    code: str
    message: str
    request_id: str
    retryable: bool = False
    field_errors: dict[str, str] = field(default_factory=dict)
    remediation: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "field_errors": dict(self.field_errors),
            "remediation": self.remediation,
        }


class DomainError(Exception):
    code: str = "domain_error"
    retryable: bool = False
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        field_errors: dict[str, str] | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.field_errors = field_errors or {}
        self.remediation = remediation

    def to_envelope(self, request_id: str) -> ErrorEnvelope:
        return ErrorEnvelope(
            code=self.code,
            message=str(self),
            request_id=request_id,
            retryable=self.retryable,
            field_errors=self.field_errors,
            remediation=self.remediation,
        )


def public_message(exc: BaseException) -> str:
    if isinstance(exc, DomainError):
        return str(exc)[:500]
    return f"The step failed with an unexpected {exc.__class__.__name__}; details are in the server log."


class ValidationError(DomainError):
    code = "validation_error"
    http_status = 422


class LimitExceededError(DomainError):
    code = "limit_exceeded"
    http_status = 413


class SecurityPolicyError(DomainError):
    code = "security_policy_violation"
    http_status = 403


class PersistenceUnavailableError(DomainError):
    code = "persistence_unavailable"
    retryable = True
    http_status = 503


class ConnectorError(DomainError):
    code = "connector_error"
    http_status = 502


class TransactionLostError(ConnectorError):
    code = "transaction_lost"
    retryable = True


class SchemaDriftError(DomainError):
    code = "schema_drift_detected"
    http_status = 409


class QueryValidationError(DomainError):
    code = "query_validation_error"
    http_status = 400


class QueryTimeoutError(DomainError):
    code = "query_timeout"


class BudgetExhaustedError(DomainError):
    code = "budget_exhausted"
    http_status = 429


class ClarificationRequired(Exception):
    def __init__(self, prompt: str) -> None:
        super().__init__(prompt)
        self.prompt = prompt


class ProviderError(DomainError):
    code = "provider_error"
    http_status = 502


class CircuitOpenError(ProviderError):
    code = "circuit_open"
    retryable = True
    http_status = 503


class IdempotencyConflictError(DomainError):
    code = "idempotency_conflict"
    http_status = 409


class VersionConflictError(DomainError):
    code = "version_conflict"
    http_status = 409


class NotFoundError(DomainError):
    code = "not_found"
    http_status = 404
