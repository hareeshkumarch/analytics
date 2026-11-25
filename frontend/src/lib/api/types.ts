import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

export type ApproveSemanticsRequest = Schemas["ApproveSemanticsRequest"];
export type ClarifyRunRequest = Schemas["ClarifyRunRequest"];
export type ConnectorExtractRequest = Schemas["ConnectorExtractRequest"];
export type ConnectorFamily = Schemas["ConnectorFamily"];
export type ConnectorProbeRequest = Schemas["ConnectorProbeRequest"];
export type CreateExportRequest = Schemas["CreateExportRequest"];
export type CreatePinRequest = Schemas["CreatePinRequest"];
export type CreateRunRequest = Schemas["CreateRunRequest"];
export type ImportForm = Schemas["ImportForm"];
export type ProviderId = Schemas["ProviderId"];
export type RunMode = Schemas["RunMode"];
export type SaveProviderKeyRequest = Schemas["SaveProviderKeyRequest"];
export type UpdatePinRequest = Schemas["UpdatePinRequest"];
export type UpdateRoutingRequest = Schemas["UpdateRoutingRequest"];

export type ChartOut = Schemas["ChartOut"];
export type ColumnDetailOut = Schemas["ColumnDetailOut"];
export type CreateRunOut = Schemas["CreateRunOut"];
export type ModelStatusOut = Schemas["ModelStatusOut"];
export type ModelUsageOut = Schemas["ModelUsageOut"];
export type PinOut = Schemas["PinOut"];
export type ProviderStatusOut = Schemas["ProviderStatusOut"];
export type ProvidersOut = Schemas["ProvidersOut"];
export type RoutingPolicyOut = Schemas["RoutingPolicyOut"];
export type RoutingPreset = Schemas["RoutingPreset"];
export type RunOut = Schemas["RunOut"];
export type RunPageOut = Schemas["RunPageOut"];
export type RunUsageOut = Schemas["RunUsageOut"];
export type SnapshotOut = Schemas["SnapshotOut"];
export type UsageOut = Schemas["UsageOut"];
