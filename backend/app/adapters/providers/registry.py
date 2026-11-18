from app.application.ports.providers import ModelGateway
from app.domain.errors import ValidationError
from app.domain.providers import ProviderId


class ProviderRegistry:
    def __init__(self) -> None:
        self._gateways: dict[ProviderId, ModelGateway] = {}

    def register(self, gateway: ModelGateway) -> None:
        self._gateways[gateway.provider_id] = gateway

    def get(self, provider_id: ProviderId) -> ModelGateway:
        if provider_id not in self._gateways:
            raise ValidationError(f"Unsupported provider: {provider_id.value}")
        return self._gateways[provider_id]

    def supported_providers(self) -> tuple[ProviderId, ...]:
        return tuple(self._gateways.keys())
