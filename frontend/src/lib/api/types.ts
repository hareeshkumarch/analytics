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
