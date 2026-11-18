from app.application.ports.connectors import ConnectorDiscovery, SnapshotExtractor
from app.domain.errors import ValidationError
from app.domain.models import ConnectorFamily


class ConnectorRegistry:
    def __init__(self) -> None:
        self._discovery: dict[ConnectorFamily, ConnectorDiscovery] = {}
        self._extractors: dict[ConnectorFamily, SnapshotExtractor] = {}

    def register(
        self,
        family: ConnectorFamily,
        discovery: ConnectorDiscovery,
        extractor: SnapshotExtractor,
    ) -> None:
        self._discovery[family] = discovery
        self._extractors[family] = extractor

    def get_discovery(self, family: ConnectorFamily) -> ConnectorDiscovery:
        if family not in self._discovery:
            raise ValidationError(f"Unsupported connector family: {family.value}")
        return self._discovery[family]

    def get_extractor(self, family: ConnectorFamily) -> SnapshotExtractor:
        if family not in self._extractors:
            raise ValidationError(f"Unsupported connector family: {family.value}")
        return self._extractors[family]

    def supported_families(self) -> tuple[ConnectorFamily, ...]:
        return tuple(self._discovery.keys())
