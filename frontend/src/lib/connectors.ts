import type { ConnectorFamily } from "@/lib/api/types";
import type { RichOption } from "@/lib/types";

export type Family = ConnectorFamily;

/** Catalog entries. Several entries are presets of one backend family (e.g. Supabase → postgresql). */
export type ConnectorId =
  | "file"
  | "remote_url"
  | "s3"
  | "gcs"
  | "azure_blob"
  | "sheets_public"
  | "postgresql"
  | "supabase"
  | "neon"
  | "aws_rds_postgres"
  | "cloud_sql_postgres"
  | "azure_postgres"
  | "mysql"
  | "mariadb"
  | "aws_rds_mysql"
  | "azure_mysql"
  | "google_sheets"
  | "airtable"
  | "bigquery"
  | "rest_json"
  | "hubspot"
  | "stripe"
  | "intercom"
  | "github";

export type ConnectorCategory = "files" | "storage" | "databases" | "warehouses" | "apps" | "apis";

export const CATEGORY_LABELS: Record<ConnectorCategory, string> = {
  files: "Files",
  storage: "Cloud storage & links",
  databases: "Databases",
  warehouses: "Warehouses",
  apps: "Business apps",
  apis: "APIs",
};

export type FieldValue = string | boolean;
export type Values = Record<string, FieldValue>;

export interface FieldDef {
  key: string;
  label: string;
  type?: "text" | "password" | "number" | "url" | "textarea" | "checkbox" | "select";
  placeholder?: string;
  required?: boolean;
  help?: string;
  secret?: boolean;
  defaultValue?: FieldValue;
  options?: RichOption[];
  half?: boolean;
  readOnly?: boolean;
  /** Used only to build the config (see ConnectorDef.buildConfig); never sent as its own key. */
  virtual?: boolean;
  showWhen?: (values: Values) => boolean;
  /** Returns a message when a non-empty value is invalid. */
  validate?: (value: string, values: Values) => string | null;
}

export interface ConnectorDef {
  id: ConnectorId;
  family: Family;
  label: string;
  sublabel: string;
  category: ConnectorCategory;
  authSummary: string;
  note?: string;
  /** Shown when this entry is a preset of a more general connector. */
  basedOn?: string;
  fields: FieldDef[];
  fixedConfig?: Record<string, unknown>;
  buildConfig?: (values: Values) => Record<string, unknown>;
  keywords?: string[];
}

export interface SourcePayload {
  family: Family;
  config: Record<string, unknown>;
  credentials: Record<string, string>;
}

const str = (v: FieldValue | undefined) => (typeof v === "string" ? v.trim() : "");

const hostOnly = (value: string) =>
  /^[a-z]+:\/\//i.test(value) || /[/\s]/.test(value) || /:\d+$/.test(value)
    ? "Enter the host name only, without https://, a path or a port."
    : null;

const httpsUrl = (value: string) => {
  try {
    const u = new URL(value);
    return u.protocol === "https:" ? null : "The link must start with https://";
  } catch {
    return "Enter a full link, starting with https://";
  }
};

const DATASET_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
export const datasetName = (value: string) =>
  DATASET_NAME_RE.test(value) ? null : "Use letters, numbers and underscores, starting with a letter (max 64).";

export function sanitizeDatasetName(raw: string): string {
  const cleaned = raw.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64);
  if (!cleaned) return "dataset";
  return /^[A-Za-z_]/.test(cleaned) ? cleaned : `t_${cleaned}`.slice(0, 64);
}

const SSL_OPTIONS: RichOption[] = [
  { value: "require", label: "require", sublabel: "Encrypt; don't verify the certificate" },
  { value: "verify-full", label: "verify-full", sublabel: "Encrypt and verify host + certificate" },
  { value: "verify-ca", label: "verify-ca", sublabel: "Encrypt and verify the certificate" },
  { value: "prefer", label: "prefer", sublabel: "Encrypt if the server supports it" },
  { value: "disable", label: "disable", sublabel: "No TLS: local databases only" },
];

const MYSQL_TLS_OPTIONS: RichOption[] = [
  { value: "require", label: "require", sublabel: "Encrypt; don't verify the certificate" },
  { value: "verify-full", label: "verify-full", sublabel: "Encrypt and verify host + certificate" },
  { value: "disable", label: "disable", sublabel: "No TLS: local databases only" },
];

const PAGINATION_OPTIONS: RichOption[] = [
  { value: "none", label: "No pagination", sublabel: "One request returns every record" },
  { value: "cursor", label: "Cursor", sublabel: "Response holds a next-cursor value" },
  { value: "next_link", label: "Next link in body", sublabel: "Response holds the URL of the next page" },
  { value: "link_header", label: "Link header", sublabel: "rel=\"next\" in the Link header (GitHub style)" },
  { value: "last_id", label: "Last record id", sublabel: "has_more + starting_after (Stripe style)" },
];

const FORMAT_OPTIONS: RichOption[] = [
  { value: "auto", label: "Detect automatically", sublabel: "From the link or the server's content type" },
  { value: "csv", label: "CSV" },
  { value: "tsv", label: "TSV (tab-separated)" },
  { value: "xlsx", label: "Excel (.xlsx)" },
  { value: "json", label: "JSON" },
  { value: "ndjson", label: "NDJSON / JSON Lines" },
  { value: "parquet", label: "Parquet" },
  { value: "arrow", label: "Arrow / Feather" },
];

function postgresFields(o: { hostPlaceholder: string; hostHelp?: string; database?: string; username?: string; sslmode?: string }): FieldDef[] {
  return [
    { key: "host", label: "Host", placeholder: o.hostPlaceholder, required: true, half: true, help: o.hostHelp, validate: hostOnly },
    {
      key: "port",
      label: "Port",
      type: "number",
      defaultValue: "5432",
      required: true,
      half: true,
      readOnly: true,
      help: "Fixed: the server only allows port 5432.",
    },
    { key: "database", label: "Database", placeholder: "analytics", defaultValue: o.database ?? "", required: true, half: true },
    { key: "schema", label: "Schema", defaultValue: "public", half: true },
    { key: "sslmode", label: "TLS mode", type: "select", defaultValue: o.sslmode ?? "require", options: SSL_OPTIONS },
    { key: "username", label: "Username", secret: true, required: true, half: true, defaultValue: o.username ?? "" },
    { key: "password", label: "Password", type: "password", secret: true, required: true, half: true },
  ];
}

function mysqlFields(o: { hostPlaceholder: string; tls: string }): FieldDef[] {
  return [
    { key: "host", label: "Host", placeholder: o.hostPlaceholder, required: true, half: true, validate: hostOnly },
    {
      key: "port",
      label: "Port",
      type: "number",
      defaultValue: "3306",
      required: true,
      half: true,
      readOnly: true,
      help: "Fixed: the server only allows port 3306.",
    },
    { key: "database", label: "Database", placeholder: "analytics", required: true, half: true },
    { key: "tls_mode", label: "TLS mode", type: "select", defaultValue: o.tls, options: MYSQL_TLS_OPTIONS, half: true },
    { key: "username", label: "Username", secret: true, required: true, half: true },
    { key: "password", label: "Password", type: "password", secret: true, required: true, half: true },
  ];
}

function remoteFields(o: { urlLabel: string; placeholder: string; help: string; token?: boolean }): FieldDef[] {
  const fields: FieldDef[] = [
    { key: "url", label: o.urlLabel, type: "url", required: true, placeholder: o.placeholder, help: o.help, validate: httpsUrl },
    { key: "format", label: "File format", type: "select", defaultValue: "auto", options: FORMAT_OPTIONS, half: true },
    {
      key: "table_name",
      label: "Dataset name (optional)",
      placeholder: "orders",
      half: true,
      validate: datasetName,
      help: "Defaults to the file name.",
    },
  ];
  if (o.token) {
    fields.push({
      key: "bearer_token",
      label: "Bearer token (optional)",
      type: "password",
      secret: true,
      help: "Sent only to this host, never after a redirect to another host.",
    });
  }
  return fields;
}

/** Turn any Google Sheets link into its CSV export link, or null if it isn't one. */
export function toSheetsCsvUrl(link: string): string | null {
  let u: URL;
  try {
    u = new URL(link.trim());
  } catch {
    return null;
  }
  if (u.hostname !== "docs.google.com") return null;
  const gid = u.searchParams.get("gid") ?? /gid=(\d+)/.exec(u.hash)?.[1] ?? null;
  const published = /^\/spreadsheets\/d\/e\/([A-Za-z0-9_-]+)/.exec(u.pathname);
  if (published) {
    const out = new URL(`https://docs.google.com/spreadsheets/d/e/${published[1]}/pub`);
    out.searchParams.set("output", "csv");
    if (gid) out.searchParams.set("gid", gid);
    return out.toString();
  }
  const sheet = /^\/spreadsheets\/d\/([A-Za-z0-9_-]+)/.exec(u.pathname);
  if (!sheet) return null;
  return `https://docs.google.com/spreadsheets/d/${sheet[1]}/export?format=csv&gid=${gid ?? "0"}`;
}

const restDefaults = { safe_get_documented: true };

const HUBSPOT_OBJECTS: RichOption[] = ["contacts", "companies", "deals", "tickets", "products", "line_items"].map((o) => ({
  value: o,
  label: o.replace("_", " ").replace(/^\w/, (c) => c.toUpperCase()),
}));

const STRIPE_RESOURCES: RichOption[] = [
  "charges",
  "customers",
  "invoices",
  "payment_intents",
  "refunds",
  "subscriptions",
  "payouts",
  "balance_transactions",
].map((r) => ({ value: r, label: r.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase()) }));

const GITHUB_RESOURCES: RichOption[] = [
  { value: "issues", label: "Issues", sublabel: "Open and closed, includes pull requests" },
  { value: "pulls", label: "Pull requests", sublabel: "Open and closed" },
  { value: "commits", label: "Commits", sublabel: "Default branch" },
  { value: "releases", label: "Releases" },
  { value: "contributors", label: "Contributors" },
];

export const CONNECTORS: ConnectorDef[] = [
  {
    id: "file",
    family: "uploaded_file",
    label: "Upload a file",
    sublabel: "CSV, TSV, Excel, JSON, Parquet, Arrow, SQLite or DuckDB",
    category: "files",
    authSummary: "No credentials",
    note: "Drop a file or paste CSV text. Each upload becomes an isolated, read-only snapshot.",
    fields: [],
    keywords: ["csv", "excel", "xlsx", "json", "ndjson", "parquet", "arrow", "feather", "sqlite", "duckdb", "tsv", "upload"],
  },
  {
    id: "remote_url",
    family: "remote_file",
    label: "File from a link",
    sublabel: "Any public HTTPS link to a CSV, Excel, JSON, Parquet or Arrow file",
    category: "storage",
    authSummary: "Public link, optional bearer token",
    note: "Downloads over HTTPS only. Private network addresses are blocked and redirects are re-checked.",
    fields: remoteFields({
      urlLabel: "File link",
      placeholder: "https://example.com/exports/orders.csv",
      help: "Must be a direct download link, not a web page.",
      token: true,
    }),
    keywords: ["url", "http", "https", "download", "link", "github raw", "dropbox"],
  },
  {
    id: "s3",
    family: "remote_file",
    label: "Amazon S3",
    sublabel: "Import an object with a pre-signed URL",
    category: "storage",
    authSummary: "Pre-signed URL",
    basedOn: "File from a link",
    note: "Create a short-lived link with: aws s3 presign s3://bucket/key --expires-in 3600. The link's signature is never stored.",
    fields: remoteFields({
      urlLabel: "Pre-signed URL",
      placeholder: "https://bucket.s3.amazonaws.com/orders.parquet?X-Amz-…",
      help: "Paste the whole link, including everything after the ?",
    }),
    keywords: ["aws", "s3", "bucket", "presigned"],
  },
  {
    id: "gcs",
    family: "remote_file",
    label: "Google Cloud Storage",
    sublabel: "Import an object with a signed URL",
    category: "storage",
    authSummary: "Signed URL",
    basedOn: "File from a link",
    note: "Create one with: gcloud storage sign-url gs://bucket/object --duration=1h. The signature is never stored.",
    fields: remoteFields({
      urlLabel: "Signed URL",
      placeholder: "https://storage.googleapis.com/bucket/orders.csv?X-Goog-…",
      help: "Paste the whole link, including everything after the ?",
    }),
    keywords: ["gcp", "gcs", "bucket", "signed", "google cloud"],
  },
  {
    id: "azure_blob",
    family: "remote_file",
    label: "Azure Blob Storage",
    sublabel: "Import a blob with a SAS URL",
    category: "storage",
    authSummary: "Shared access signature (SAS) URL",
    basedOn: "File from a link",
    note: "Generate a read-only SAS for the blob in the Azure portal. The SAS token is never stored.",
    fields: remoteFields({
      urlLabel: "Blob SAS URL",
      placeholder: "https://account.blob.core.windows.net/container/orders.csv?sv=…",
      help: "Paste the whole link, including everything after the ?",
    }),
    keywords: ["azure", "blob", "sas", "microsoft"],
  },
  {
    id: "sheets_public",
    family: "remote_file",
    label: "Google Sheets (shared link)",
    sublabel: "One tab of a sheet shared as \"Anyone with the link\"",
    category: "apps",
    authSummary: "Shared link, no key",
    basedOn: "File from a link",
    note: "Share the sheet as \"Anyone with the link: Viewer\", open the tab you want, and copy the address bar. For private sheets use Google Sheets (service account).",
    fields: [
      {
        key: "sheet_url",
        label: "Google Sheets link",
        type: "url",
        required: true,
        virtual: true,
        placeholder: "https://docs.google.com/spreadsheets/d/…/edit#gid=0",
        validate: (v) => (toSheetsCsvUrl(v) ? null : "Paste a docs.google.com/spreadsheets link."),
      },
      { key: "table_name", label: "Dataset name (optional)", placeholder: "budget", validate: datasetName },
    ],
    buildConfig: (v) => ({ url: toSheetsCsvUrl(str(v.sheet_url)) ?? "", format: "csv" }),
    keywords: ["google", "sheets", "spreadsheet", "gsheet", "public"],
  },
  {
    id: "google_sheets",
    family: "google_sheets",
    label: "Google Sheets (service account)",
    sublabel: "Private sheets, every tab, read-only",
    category: "apps",
    authSummary: "Google service account JSON key",
    note: "Share the spreadsheet with the service account's client_email as Viewer first. The key is used for this import and is not stored.",
    fields: [
      {
        key: "spreadsheet_id",
        label: "Spreadsheet ID",
        placeholder: "1AbC…xyz",
        required: true,
        help: "The long ID between /d/ and /edit in the sheet's URL.",
        validate: (v) => (/^[A-Za-z0-9_-]{20,}$/.test(v) ? null : "That doesn't look like a spreadsheet ID."),
      },
      {
        key: "service_account_json",
        label: "Service account key (JSON)",
        type: "textarea",
        secret: true,
        required: true,
        placeholder: '{ "type": "service_account", … }',
        validate: (v) => {
          try {
            const parsed = JSON.parse(v) as Record<string, unknown>;
            return parsed.type === "service_account" ? null : "This JSON isn't a service account key.";
          } catch {
            return "Paste the whole JSON key file.";
          }
        },
      },
    ],
    keywords: ["google", "sheets", "spreadsheet", "gsheet", "service account"],
  },
  {
    id: "airtable",
    family: "airtable",
    label: "Airtable",
    sublabel: "Every table of a base, paginated",
    category: "apps",
    authSummary: "Personal access token (PAT)",
    note: "Create a base-scoped PAT with data.records:read and schema.bases:read. The token is used for this import and is not stored.",
    fields: [
      {
        key: "base_id",
        label: "Base ID",
        placeholder: "appXXXXXXXXXXXXXX",
        required: true,
        help: "Find the app… ID in the Airtable base URL.",
        validate: (v) => (/^app[A-Za-z0-9]{14}$/.test(v) ? null : "Base IDs look like app followed by 14 characters."),
      },
      {
        key: "personal_access_token",
        label: "Personal access token",
        type: "password",
        secret: true,
        required: true,
        placeholder: "pat…",
        help: "Use only read scopes and restrict the token to this base.",
      },
    ],
    keywords: ["airtable", "base", "no-code"],
  },
  {
    id: "postgresql",
    family: "postgresql",
    label: "PostgreSQL",
    sublabel: "Read-only snapshot in one repeatable-read transaction",
    category: "databases",
    authSummary: "Database username and password",
    note: "Use a read-only database user. The password is sent for this import and never stored.",
    fields: postgresFields({ hostPlaceholder: "db.internal.example" }),
    keywords: ["postgres", "pg", "sql", "timescale"],
  },
  {
    id: "supabase",
    family: "postgresql",
    label: "Supabase",
    sublabel: "Your project's Postgres database",
    category: "databases",
    authSummary: "Database password",
    basedOn: "PostgreSQL",
    note: "Settings → Database → Connection string. Use the direct connection or the Session pooler (port 5432); the transaction pooler (6543) is blocked. On IPv4-only networks use the Session pooler host and the user postgres.<project-ref>.",
    fields: postgresFields({
      hostPlaceholder: "db.<project-ref>.supabase.co",
      database: "postgres",
      username: "postgres",
      sslmode: "require",
    }),
    keywords: ["supabase", "postgres"],
  },
  {
    id: "neon",
    family: "postgresql",
    label: "Neon",
    sublabel: "Serverless Postgres branch",
    category: "databases",
    authSummary: "Role password",
    basedOn: "PostgreSQL",
    note: "Use the host from the connection details (not the -pooler host). TLS is required.",
    fields: postgresFields({ hostPlaceholder: "ep-…-123456.us-east-2.aws.neon.tech", database: "neondb", sslmode: "require" }),
    keywords: ["neon", "postgres", "serverless"],
  },
  {
    id: "aws_rds_postgres",
    family: "postgresql",
    label: "Amazon RDS / Aurora PostgreSQL",
    sublabel: "Managed PostgreSQL on AWS",
    category: "databases",
    authSummary: "Database username and password",
    basedOn: "PostgreSQL",
    note: "The instance must be publicly reachable from this machine. Use a read-only user.",
    fields: postgresFields({ hostPlaceholder: "mydb.abc123.eu-west-1.rds.amazonaws.com", database: "postgres" }),
    keywords: ["aws", "rds", "aurora", "amazon", "postgres"],
  },
  {
    id: "cloud_sql_postgres",
    family: "postgresql",
    label: "Google Cloud SQL (PostgreSQL)",
    sublabel: "Managed PostgreSQL on Google Cloud",
    category: "databases",
    authSummary: "Database username and password",
    basedOn: "PostgreSQL",
    note: "Use the instance's public IP and add this machine to Authorized networks.",
    fields: postgresFields({ hostPlaceholder: "34.123.45.67", database: "postgres" }),
    keywords: ["gcp", "cloud sql", "google", "postgres"],
  },
  {
    id: "azure_postgres",
    family: "postgresql",
    label: "Azure Database for PostgreSQL",
    sublabel: "Flexible server",
    category: "databases",
    authSummary: "Database username and password",
    basedOn: "PostgreSQL",
    note: "Allow this machine's IP in Networking. TLS is required.",
    fields: postgresFields({ hostPlaceholder: "myserver.postgres.database.azure.com", database: "postgres", sslmode: "require" }),
    keywords: ["azure", "microsoft", "postgres"],
  },
  {
    id: "mysql",
    family: "mysql",
    label: "MySQL",
    sublabel: "Read-only snapshot with a consistent read",
    category: "databases",
    authSummary: "Database username and password",
    note: "Use a read-only database user. The password is sent for this import and never stored.",
    fields: mysqlFields({ hostPlaceholder: "db.internal.example", tls: "disable" }),
    keywords: ["mysql", "sql"],
  },
  {
    id: "mariadb",
    family: "mysql",
    label: "MariaDB",
    sublabel: "MySQL-compatible server",
    category: "databases",
    authSummary: "Database username and password",
    basedOn: "MySQL",
    fields: mysqlFields({ hostPlaceholder: "mariadb.internal.example", tls: "disable" }),
    keywords: ["mariadb", "mysql"],
  },
  {
    id: "aws_rds_mysql",
    family: "mysql",
    label: "Amazon RDS / Aurora MySQL",
    sublabel: "Managed MySQL or MariaDB on AWS",
    category: "databases",
    authSummary: "Database username and password",
    basedOn: "MySQL",
    note: "The instance must be publicly reachable. \"require\" encrypts without checking the RDS certificate authority.",
    fields: mysqlFields({ hostPlaceholder: "mydb.abc123.eu-west-1.rds.amazonaws.com", tls: "require" }),
    keywords: ["aws", "rds", "aurora", "amazon", "mysql", "mariadb"],
  },
  {
    id: "azure_mysql",
    family: "mysql",
    label: "Azure Database for MySQL",
    sublabel: "Flexible server",
    category: "databases",
    authSummary: "Database username and password",
    basedOn: "MySQL",
    note: "Allow this machine's IP in Networking. Azure requires TLS by default.",
    fields: mysqlFields({ hostPlaceholder: "myserver.mysql.database.azure.com", tls: "verify-full" }),
    keywords: ["azure", "microsoft", "mysql"],
  },
  {
    id: "bigquery",
    family: "bigquery",
    label: "Google BigQuery",
    sublabel: "Tables of one dataset, via a service account",
    category: "warehouses",
    authSummary: "Google service account JSON key",
    note: "Grant the service account BigQuery Data Viewer on this dataset only. The key is used for this import and is not stored.",
    fields: [
      {
        key: "project_id",
        label: "Google Cloud project ID",
        placeholder: "analytics-prod",
        required: true,
        half: true,
      },
      {
        key: "dataset_id",
        label: "BigQuery dataset ID",
        placeholder: "sales_reporting",
        required: true,
        half: true,
      },
      { key: "location", label: "Location (optional)", placeholder: "US", half: true },
      {
        key: "service_account_json",
        label: "Service account key (JSON)",
        type: "textarea",
        secret: true,
        required: true,
        placeholder: '{ "type": "service_account", … }',
      },
    ],
    keywords: ["google", "bigquery", "bq", "warehouse", "gcp"],
  },
  {
    id: "hubspot",
    family: "rest_json",
    label: "HubSpot CRM",
    sublabel: "Contacts, companies, deals, tickets…",
    category: "apps",
    authSummary: "Private app access token",
    basedOn: "REST JSON API",
    note: "Create a private app with read-only scopes for the chosen object. Object properties arrive as one text column.",
    fields: [
      { key: "object", label: "Object", type: "select", virtual: true, defaultValue: "contacts", options: HUBSPOT_OBJECTS },
      {
        key: "bearer_token",
        label: "Access token",
        type: "password",
        secret: true,
        required: true,
        placeholder: "pat-…",
      },
    ],
    buildConfig: (v) => {
      const object = str(v.object) || "contacts";
      return {
        ...restDefaults,
        url: `https://api.hubapi.com/crm/v3/objects/${encodeURIComponent(object)}?limit=100`,
        collection_path: "results",
        pagination_mode: "cursor",
        next_cursor_path: "paging.next.after",
        cursor_param: "after",
        table_name: `hubspot_${object}`,
      };
    },
    keywords: ["hubspot", "crm", "contacts", "deals", "marketing"],
  },
  {
    id: "stripe",
    family: "rest_json",
    label: "Stripe",
    sublabel: "Charges, customers, invoices, payouts…",
    category: "apps",
    authSummary: "Restricted API key (read-only)",
    basedOn: "REST JSON API",
    note: "Create a restricted key with Read access to the chosen resource only. Never paste a full secret key.",
    fields: [
      { key: "resource", label: "Resource", type: "select", virtual: true, defaultValue: "charges", options: STRIPE_RESOURCES },
      {
        key: "bearer_token",
        label: "Restricted key",
        type: "password",
        secret: true,
        required: true,
        placeholder: "rk_live_…",
        validate: (v) => (v.startsWith("sk_") ? "Use a restricted key (rk_…), not a full secret key." : null),
      },
    ],
    buildConfig: (v) => {
      const resource = str(v.resource) || "charges";
      return {
        ...restDefaults,
        url: `https://api.stripe.com/v1/${encodeURIComponent(resource)}?limit=100`,
        collection_path: "data",
        pagination_mode: "last_id",
        cursor_param: "starting_after",
        has_more_path: "has_more",
        id_field: "id",
        table_name: `stripe_${resource}`,
      };
    },
    keywords: ["stripe", "payments", "billing", "invoices", "finance"],
  },
  {
    id: "intercom",
    family: "rest_json",
    label: "Intercom",
    sublabel: "Contacts or conversations",
    category: "apps",
    authSummary: "Access token",
    basedOn: "REST JSON API",
    note: "Use an access token from a private Intercom app with read permissions.",
    fields: [
      {
        key: "resource",
        label: "Resource",
        type: "select",
        virtual: true,
        defaultValue: "contacts",
        options: [
          { value: "contacts", label: "Contacts" },
          { value: "conversations", label: "Conversations" },
        ],
      },
      { key: "bearer_token", label: "Access token", type: "password", secret: true, required: true },
    ],
    buildConfig: (v) => {
      const resource = str(v.resource) === "conversations" ? "conversations" : "contacts";
      return {
        ...restDefaults,
        url: `https://api.intercom.io/${resource}?per_page=150`,
        collection_path: resource === "conversations" ? "conversations" : "data",
        pagination_mode: "cursor",
        next_cursor_path: "pages.next.starting_after",
        cursor_param: "starting_after",
        table_name: `intercom_${resource}`,
      };
    },
    keywords: ["intercom", "support", "customers", "conversations"],
  },
  {
    id: "github",
    family: "rest_json",
    label: "GitHub",
    sublabel: "Issues, pull requests, commits or releases of a repository",
    category: "apis",
    authSummary: "Optional fine-grained token",
    basedOn: "REST JSON API",
    note: "Public repositories work without a token (60 requests per hour). Imports stop at 50 pages of 100 records.",
    fields: [
      {
        key: "owner",
        label: "Owner",
        placeholder: "vercel",
        required: true,
        virtual: true,
        half: true,
        validate: (v) => (/^[A-Za-z0-9-]{1,39}$/.test(v) ? null : "Enter the user or organization name only."),
      },
      {
        key: "repo",
        label: "Repository",
        placeholder: "next.js",
        required: true,
        virtual: true,
        half: true,
        validate: (v) => (/^[A-Za-z0-9._-]{1,100}$/.test(v) ? null : "Enter the repository name only."),
      },
      { key: "resource", label: "Records", type: "select", virtual: true, defaultValue: "issues", options: GITHUB_RESOURCES },
      {
        key: "bearer_token",
        label: "Token (optional)",
        type: "password",
        secret: true,
        help: "A fine-grained token with read-only access to this repository.",
      },
    ],
    buildConfig: (v) => {
      const resource = str(v.resource) || "issues";
      const state = resource === "issues" || resource === "pulls" ? "&state=all" : "";
      return {
        ...restDefaults,
        url: `https://api.github.com/repos/${encodeURIComponent(str(v.owner))}/${encodeURIComponent(str(v.repo))}/${resource}?per_page=100${state}`,
        collection_path: "$",
        pagination_mode: "link_header",
        table_name: `github_${resource}`,
      };
    },
    keywords: ["github", "git", "issues", "pull requests", "engineering"],
  },
  {
    id: "rest_json",
    family: "rest_json",
    label: "REST JSON API",
    sublabel: "Any documented HTTPS GET endpoint that returns JSON",
    category: "apis",
    authSummary: "Optional bearer token",
    note: "Only documented, side-effect-free GET endpoints. Redirects and private addresses are blocked.",
    fields: [
      { key: "url", label: "Endpoint URL", type: "url", placeholder: "https://api.example.com/v1/records", required: true, validate: httpsUrl },
      {
        key: "collection_path",
        label: "Records path",
        placeholder: "data.items",
        required: true,
        help: "Dotted path to the array of records. Use $ when the response itself is the array.",
        half: true,
      },
      { key: "table_name", label: "Dataset name", placeholder: "records", half: true, validate: datasetName },
      { key: "pagination_mode", label: "Pagination", type: "select", defaultValue: "none", options: PAGINATION_OPTIONS },
      {
        key: "next_cursor_path",
        label: "Next-cursor path",
        defaultValue: "next_cursor",
        half: true,
        showWhen: (v) => v.pagination_mode === "cursor",
      },
      {
        key: "cursor_param",
        label: "Cursor query parameter",
        defaultValue: "cursor",
        half: true,
        showWhen: (v) => v.pagination_mode === "cursor" || v.pagination_mode === "last_id",
      },
      { key: "next_link_path", label: "Next-link path", defaultValue: "next", half: true, showWhen: (v) => v.pagination_mode === "next_link" },
      { key: "has_more_path", label: "Has-more path", defaultValue: "has_more", half: true, showWhen: (v) => v.pagination_mode === "last_id" },
      { key: "id_field", label: "Record id field", defaultValue: "id", half: true, showWhen: (v) => v.pagination_mode === "last_id" },
      { key: "bearer_token", label: "Bearer token (optional)", type: "password", secret: true, half: true },
      {
        key: "safe_get_documented",
        label: "This endpoint is a documented, read-only GET",
        type: "checkbox",
        required: true,
        help: "Required: the connector only calls endpoints that have no side effects.",
      },
    ],
    keywords: ["rest", "api", "json", "http", "endpoint", "webhook"],
  },
];

export const CONNECTOR_BY_ID = new Map<ConnectorId, ConnectorDef>(CONNECTORS.map((c) => [c.id, c]));

export function familyLabel(family: string | null | undefined): string {
  const labels: Record<string, string> = {
    uploaded_file: "File upload",
    uploaded_sqlite: "SQLite file",
    uploaded_duckdb: "DuckDB file",
    remote_file: "File link",
    postgresql: "PostgreSQL",
    mysql: "MySQL",
    google_sheets: "Google Sheets",
    rest_json: "REST API",
    airtable: "Airtable",
    bigquery: "BigQuery",
  };
  return (family && labels[family]) || "Other source";
}

export function isFieldVisible(field: FieldDef, values: Values): boolean {
  return field.showWhen ? field.showWhen(values) : true;
}

export function initialValues(def: ConnectorDef): Values {
  const out: Values = {};
  for (const f of def.fields) out[f.key] = f.defaultValue ?? (f.type === "checkbox" ? false : "");
  return out;
}

export function toPayload(def: ConnectorDef, values: Values): SourcePayload {
  const config: Record<string, unknown> = { ...(def.fixedConfig ?? {}) };
  const credentials: Record<string, string> = {};
  for (const f of def.fields) {
    if (!isFieldVisible(f, values) || f.virtual) continue;
    const v = values[f.key];
    if (v === "" || v === undefined) continue;
    if (f.secret) credentials[f.key] = String(v);
    else if (f.type === "number") config[f.key] = Number(v);
    else config[f.key] = typeof v === "string" ? v.trim() : v;
  }
  if (def.buildConfig) Object.assign(config, def.buildConfig(values));
  return { family: def.family, config, credentials };
}

export function missingRequired(def: ConnectorDef, values: Values): string[] {
  return def.fields
    .filter((f) => f.required && isFieldVisible(f, values))
    .filter((f) => (f.type === "checkbox" ? values[f.key] !== true : !String(values[f.key] ?? "").trim()))
    .map((f) => f.label);
}

/** Client-side validation errors for visible, non-empty fields, keyed by field key. */
export function fieldErrors(def: ConnectorDef, values: Values): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of def.fields) {
    if (!f.validate || !isFieldVisible(f, values)) continue;
    const raw = values[f.key];
    if (typeof raw !== "string" || !raw.trim()) continue;
    const message = f.validate(raw.trim(), values);
    if (message) errors[f.key] = message;
  }
  return errors;
}

/** Connectors that are only reachable through a credentials form (probe first, then discover). */
export function needsProbe(def: ConnectorDef): boolean {
  return def.family !== "uploaded_file" && def.family !== "remote_file";
}

export interface DetectedFormat {
  family: Family;
  format: string;
  label: string;
}

export function detectFileFormat(name: string): DetectedFormat | null {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  switch (ext) {
    case "csv":
      return { family: "uploaded_file", format: "csv", label: "CSV" };
    case "tsv":
    case "tab":
      return { family: "uploaded_file", format: "tsv", label: "TSV" };
    case "xlsx":
      return { family: "uploaded_file", format: "xlsx", label: "Excel" };
    case "json":
      return { family: "uploaded_file", format: "json", label: "JSON" };
    case "ndjson":
    case "jsonl":
      return { family: "uploaded_file", format: "ndjson", label: "NDJSON" };
    case "parquet":
    case "pq":
      return { family: "uploaded_file", format: "parquet", label: "Parquet" };
    case "arrow":
    case "arrows":
    case "feather":
      return { family: "uploaded_file", format: "arrow", label: "Arrow" };
    case "sqlite":
    case "sqlite3":
    case "db":
      return { family: "uploaded_sqlite", format: "sqlite", label: "SQLite" };
    case "duckdb":
    case "ddb":
      return { family: "uploaded_duckdb", format: "duckdb", label: "DuckDB" };
    default:
      return null;
  }
}

/** Formats that always produce exactly one table named after the dataset name. */
export const SINGLE_TABLE_FORMATS = new Set(["csv", "tsv", "parquet", "arrow"]);

export const FILE_ACCEPT =
  ".csv,.tsv,.tab,.xlsx,.json,.ndjson,.jsonl,.parquet,.pq,.arrow,.arrows,.feather,.sqlite,.sqlite3,.db,.duckdb,.ddb";
