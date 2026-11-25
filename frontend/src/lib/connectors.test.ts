import { describe, expect, it } from "vitest";
import { CONNECTOR_BY_ID, detectFileFormat, initialValues, isFieldVisible, toPayload, toSheetsCsvUrl } from "@/lib/connectors";

describe("toSheetsCsvUrl", () => {
  it("turns sharing links into CSV export links", () => {
    expect(toSheetsCsvUrl("https://docs.google.com/spreadsheets/d/abc_123/edit#gid=42")).toBe(
      "https://docs.google.com/spreadsheets/d/abc_123/export?format=csv&gid=42"
    );
    expect(toSheetsCsvUrl("https://docs.google.com/spreadsheets/d/abc/edit")).toBe(
      "https://docs.google.com/spreadsheets/d/abc/export?format=csv&gid=0"
    );
    expect(toSheetsCsvUrl("https://docs.google.com/spreadsheets/d/e/2PACX-x/pubhtml?gid=7")).toBe(
      "https://docs.google.com/spreadsheets/d/e/2PACX-x/pub?output=csv&gid=7"
    );
  });

  it("rejects other hosts and non-links", () => {
    expect(toSheetsCsvUrl("https://evil.example/spreadsheets/d/abc")).toBeNull();
    expect(toSheetsCsvUrl("not a link")).toBeNull();
  });
});

describe("toPayload", () => {
  it("keeps secrets out of the config", () => {
    const def = CONNECTOR_BY_ID.get("mysql")!;
    const payload = toPayload(def, { ...initialValues(def), host: " db.example.com ", database: "shop", username: "u", password: "p" });
    expect(payload.family).toBe(def.family);
    expect(payload.credentials).toEqual({ username: "u", password: "p" });
    expect(payload.config).toMatchObject({ host: "db.example.com", database: "shop", port: 3306 });
    expect(JSON.stringify(payload.config)).not.toContain('"p"');
  });
});

describe("CA certificate field", () => {
  it("shows only for verifying TLS modes and is sent as a credential", () => {
    for (const [id, modeKey, verifying, plain] of [
      ["mysql", "tls_mode", "verify-full", "require"],
      ["postgresql", "sslmode", "verify-ca", "require"],
    ] as const) {
      const def = CONNECTOR_BY_ID.get(id)!;
      const field = def.fields.find((f) => f.key === "ca_pem")!;
      expect(isFieldVisible(field, { [modeKey]: plain })).toBe(false);
      expect(isFieldVisible(field, { [modeKey]: verifying })).toBe(true);
      const pem = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----";
      const payload = toPayload(def, { ...initialValues(def), [modeKey]: verifying, host: "db.example.com", database: "d", username: "u", password: "p", ca_pem: pem });
      expect(payload.credentials.ca_pem).toBe(pem);
      expect(payload.config).not.toHaveProperty("ca_pem");
    }
  });
});

describe("detectFileFormat", () => {
  it("maps extensions to formats", () => {
    expect(detectFileFormat("Orders.CSV")?.format).toBe("csv");
    expect(detectFileFormat("data.sqlite")?.family).toBe("uploaded_sqlite");
    expect(detectFileFormat("notes.txt")).toBeNull();
  });
});
