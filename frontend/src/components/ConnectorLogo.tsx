import React from "react";
import type { ConnectorId } from "@/lib/connectors";
import { BRAND_PATHS, BrandSlug } from "@/components/brandPaths";

/*
 * Connector logos. Where Simple Icons has the brand, this draws the official mark (see brandPaths.ts);
 * otherwise a simple drawn glyph or monogram. Each logo is a self-contained tile with its own
 * background and foreground, so it reads the same in light and dark themes (the one place fixed brand
 * colours are allowed, see docs/design.md). AWS and Microsoft marks are not available in Simple Icons,
 * so S3, RDS and the Azure connectors keep drawn marks.
 */

/** Catalog connectors plus file types that appear as dataset sources. */
export type LogoId = ConnectorId | "sqlite" | "duckdb";

type Glyph = "upload" | "link" | "bucket" | "cloud" | "braces";

type Mark =
  | { kind: "brand"; slug: BrandSlug; fg: string; bg: string }
  | { kind: "glyph"; glyph: Glyph; fg: string; bg: string }
  | { kind: "text"; text: string; fg: string; bg: string }
  | { kind: "neutral"; glyph: Glyph };

const WHITE = "#FFFFFF";

const MARKS: Record<LogoId, Mark> = {
  file: { kind: "neutral", glyph: "upload" },
  remote_url: { kind: "neutral", glyph: "link" },
  rest_json: { kind: "neutral", glyph: "braces" },

  postgresql: { kind: "brand", slug: "postgresql", fg: "#4169E1", bg: WHITE },
  supabase: { kind: "brand", slug: "supabase", fg: "#3FCF8E", bg: "#1C1C1C" },
  neon: { kind: "brand", slug: "neon", fg: "#00E599", bg: "#0C0D0D" },
  cloud_sql_postgres: { kind: "brand", slug: "googlecloud", fg: "#4285F4", bg: WHITE },
  mysql: { kind: "brand", slug: "mysql", fg: "#4479A1", bg: WHITE },
  mariadb: { kind: "brand", slug: "mariadb", fg: "#003545", bg: WHITE },
  bigquery: { kind: "brand", slug: "googlebigquery", fg: "#4285F4", bg: WHITE },
  gcs: { kind: "brand", slug: "googlecloudstorage", fg: "#4285F4", bg: WHITE },
  google_sheets: { kind: "brand", slug: "googlesheets", fg: "#34A853", bg: WHITE },
  sheets_public: { kind: "brand", slug: "googlesheets", fg: "#34A853", bg: WHITE },
  airtable: { kind: "brand", slug: "airtable", fg: "#18BFFF", bg: "#1D1F25" },
  hubspot: { kind: "brand", slug: "hubspot", fg: WHITE, bg: "#FF7A59" },
  stripe: { kind: "brand", slug: "stripe", fg: WHITE, bg: "#635BFF" },
  intercom: { kind: "brand", slug: "intercom", fg: WHITE, bg: "#1F1F1F" },
  github: { kind: "brand", slug: "github", fg: "#181717", bg: WHITE },
  sqlite: { kind: "brand", slug: "sqlite", fg: "#003B57", bg: WHITE },
  duckdb: { kind: "brand", slug: "duckdb", fg: "#FFF000", bg: "#1C1C1C" },

  // Not in Simple Icons (brand owners asked for removal): drawn marks in brand colours.
  s3: { kind: "glyph", glyph: "bucket", fg: WHITE, bg: "#3F8624" },
  azure_blob: { kind: "glyph", glyph: "cloud", fg: WHITE, bg: "#0078D4" },
  aws_rds_postgres: { kind: "text", text: "RDS", fg: "#FF9900", bg: "#232F3E" },
  aws_rds_mysql: { kind: "text", text: "RDS", fg: "#FF9900", bg: "#232F3E" },
  azure_postgres: { kind: "text", text: "Pg", fg: WHITE, bg: "#0078D4" },
  azure_mysql: { kind: "text", text: "My", fg: WHITE, bg: "#0078D4" },
};

/** Logo for a dataset's backend family (used in the Data datasets list). */
export function logoIdForFamily(family: string | null | undefined): LogoId | null {
  const map: Record<string, LogoId> = {
    uploaded_file: "file",
    uploaded_sqlite: "sqlite",
    uploaded_duckdb: "duckdb",
    remote_file: "remote_url",
    postgresql: "postgresql",
    mysql: "mysql",
    google_sheets: "google_sheets",
    rest_json: "rest_json",
    airtable: "airtable",
    bigquery: "bigquery",
  };
  return family ? map[family] ?? null : null;
}

function GlyphSvg({ name, className }: { name: Glyph; className: string }) {
  const common = {
    className,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "upload":
      return (
        <svg {...common}>
          <path d="M12 15V4M7 9l5-5 5 5" />
          <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
        </svg>
      );
    case "link":
      return (
        <svg {...common}>
          <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
          <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
        </svg>
      );
    case "bucket":
      return (
        <svg {...common}>
          <ellipse cx="12" cy="6" rx="8" ry="2.5" />
          <path d="M4 6l1.8 12.2C6 19.3 8.7 20.5 12 20.5s6-1.2 6.2-2.3L20 6" />
        </svg>
      );
    case "cloud":
      return (
        <svg {...common}>
          <path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.5a4.25 4.25 0 0 1-.5 8.5z" />
        </svg>
      );
    case "braces":
      return (
        <svg {...common}>
          <path d="M8 4c-2 0-2.5 1-2.5 3v2c0 1.5-.8 2.3-2 3 1.2.7 2 1.5 2 3v2c0 2 .5 3 2.5 3" />
          <path d="M16 4c2 0 2.5 1 2.5 3v2c0 1.5.8 2.3 2 3-1.2.7-2 1.5-2 3v2c0 2-.5 3-2.5 3" />
        </svg>
      );
  }
}

const SIZES = {
  sm: { box: "h-7 w-7", mark: "h-4 w-4", text: "text-[10px]" },
  md: { box: "h-10 w-10", mark: "h-[22px] w-[22px]", text: "text-[13px]" },
  lg: { box: "h-12 w-12", mark: "h-7 w-7", text: "text-[15px]" },
} as const;

/*
 * LLM provider logos (keys are backend ProviderId values). Anthropic, Gemini, Kimi and NVIDIA use their
 * official marks from Simple Icons; OpenAI, Groq and xAI are not in Simple Icons, so they get a plain
 * monogram rather than an imitation of their logos.
 */
const PROVIDER_MARKS: Record<string, Mark> = {
  anthropic: { kind: "brand", slug: "anthropic", fg: "#191919", bg: "#F0EEE6" },
  gemini: { kind: "brand", slug: "googlegemini", fg: "#8E75B2", bg: WHITE },
  kimi: { kind: "brand", slug: "kimi", fg: WHITE, bg: "#000000" },
  nvidia_nim: { kind: "brand", slug: "nvidia", fg: "#76B900", bg: "#1A1A1A" },
  openai: { kind: "text", text: "OA", fg: WHITE, bg: "#0D0D0D" },
  groq: { kind: "text", text: "G", fg: WHITE, bg: "#F55036" },
  xai: { kind: "text", text: "xAI", fg: WHITE, bg: "#000000" },
};

export function ProviderLogo({ providerId, size = "sm" }: { providerId: string; size?: keyof typeof SIZES }) {
  const mark: Mark = PROVIDER_MARKS[providerId] ?? { kind: "neutral", glyph: "braces" };
  return <LogoTile mark={mark} size={size} />;
}

export function ConnectorLogo({ id, size = "sm" }: { id: LogoId; size?: keyof typeof SIZES }) {
  return <LogoTile mark={MARKS[id] ?? MARKS.rest_json} size={size} />;
}

function LogoTile({ mark, size }: { mark: Mark; size: keyof typeof SIZES }) {
  const s = SIZES[size];
  const base = `inline-flex shrink-0 items-center justify-center font-bold leading-none tracking-[-0.04em] ring-1 ring-inset ring-border ${s.box}`;

  if (mark.kind === "neutral") {
    return (
      <span aria-hidden="true" className={`${base} bg-panel text-ink`}>
        <GlyphSvg name={mark.glyph} className={s.mark} />
      </span>
    );
  }

  const content =
    mark.kind === "brand" ? (
      <svg className={s.mark} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d={BRAND_PATHS[mark.slug]} />
      </svg>
    ) : mark.kind === "glyph" ? (
      <GlyphSvg name={mark.glyph} className={s.mark} />
    ) : (
      <span className={s.text}>{mark.text}</span>
    );

  return (
    <span aria-hidden="true" className={base} style={{ backgroundColor: mark.bg, color: mark.fg }}>
      {content}
    </span>
  );
}
