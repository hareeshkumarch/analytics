# Data Connectors

## Backend families

| Family | Authentication and setup | Consistency | Safeguards and limits |
| --- | --- | --- | --- |
| uploaded_file | No credentials; CSV, TSV, XLSX, JSON, NDJSON, Parquet, Arrow IPC / Feather v2 | static_export | 40 MB upload and Arrow IPC caps; leading-zero IDs preserved; nested JSON array mapping required; nested Arrow columns rejected |
| uploaded_sqlite | No credentials; upload a standalone SQLite file | static_export | WAL/journal sidecar rejection; read-only immutable quarantine; SQLite authorizer; ordinary tables only |
| uploaded_duckdb | No credentials; upload a DuckDB database file (saved by DuckDB 0.10 or newer) | static_export | Read-only quarantine copy; opened with external access, extension install/autoload and config changes disabled; base tables only (views ignored); list/struct/map/enum/UUID/interval/blob columns imported as text |
| remote_file | HTTPS link; optional bearer token | static_export | HTTPS only; private, loopback and metadata addresses blocked; connections pinned to the validated IP; up to 5 redirects, each re-validated; the token is never sent after a redirect to another origin; 40 MB streaming cap; only the host is stored, never the URL query (pre-signed signatures) |
| postgresql | Database username/password; use a read-only DB role | repeatable_read | Destination host/port policy (port 5432 only); one read-only repeatable-read transaction; quoted catalog identifiers |
| mysql | Database username/password; use a read-only DB role; `tls_mode` disable / require / verify-full | repeatable_read | Port 3306 only; InnoDB verification; consistent read-only transaction |
| google_sheets | Service account JSON; share the sheet with its email as Viewer | best_effort_window | spreadsheets.readonly; quota backoff; formatted values are not requested |
| rest_json | Optional bearer token; documented safe HTTPS GET endpoint | best_effort_window | Redirects and private/link-local IPs blocked; pagination by cursor, next link, Link header or last record id; loop detection; 50-page and 100-attempt caps; `$` as records path means the response is the array |
| airtable | Personal access token (PAT) with data.records:read and schema.bases:read, restricted to one base | best_effort_window | Read scopes only; fixed Airtable API host; offset-loop detection; up to 50 pages / 5,000 records per selected table |
| bigquery | Service account JSON, Google Cloud project ID, and one dataset ID | best_effort_window | BigQuery read-only OAuth scope; Data Viewer on the selected dataset; 1,000 tables and 1,000,000 rows maximum, within the 40 MB normalized-source cap |

## Catalog in the UI

The Data page shows 24 connection types. Several are presets of one backend family; the card says "via …" so it is clear they share a connector. They are defined in `frontend/src/lib/connectors.ts`.

| Category | Catalog entries | Backend family |
| --- | --- | --- |
| Files | Upload a file (CSV, TSV, Excel, JSON, NDJSON, Parquet, Arrow/Feather, SQLite, DuckDB) | uploaded_file / uploaded_sqlite / uploaded_duckdb |
| Cloud storage & links | File from a link, Amazon S3 (pre-signed URL), Google Cloud Storage (signed URL), Azure Blob Storage (SAS URL) | remote_file |
| Databases | PostgreSQL, Supabase, Neon, Amazon RDS / Aurora PostgreSQL, Google Cloud SQL (PostgreSQL), Azure Database for PostgreSQL | postgresql |
| Databases | MySQL, MariaDB, Amazon RDS / Aurora MySQL, Azure Database for MySQL | mysql |
| Warehouses | Google BigQuery | bigquery |
| Business apps | Google Sheets (shared link) | remote_file (CSV export link) |
| Business apps | Google Sheets (service account), Airtable | google_sheets, airtable |
| Business apps | HubSpot CRM (cursor), Stripe (last record id), Intercom (cursor) | rest_json |
| APIs | GitHub (Link header), REST JSON API | rest_json |

Overlaps by design: Google Sheets appears twice (a public "shared link" import of one tab without any key, and the service-account import of every tab of a private sheet). Supabase, Neon and the managed Postgres/MySQL entries only pre-fill host hints, defaults and TLS mode.

Not offered on purpose: connections that need a port other than 5432/3306 (Supabase transaction pooler 6543, Redshift 5439, TiDB 4000, CockroachDB 26257), APIs that need POST queries (Notion, GraphQL APIs), and APIs that need a non-bearer auth header (Shopify). Adding them means widening the destination policy or the REST connector, which should be a deliberate security decision.

Credentials are submitted for connection checks and extraction and are not written into the source configuration or snapshot manifest. Uploaded file bytes and URL query strings are removed before the source configuration is stored (migration 0002 also cleans existing rows). The connectors only perform read operations.

Setup references: [Airtable personal access tokens](https://support.airtable.com/articles/9934989703-creating-personal-access-tokens), [Airtable Web API](https://support.airtable.com/articles/6292134965-getting-started-with-airtable-s-web-api), and [BigQuery IAM roles](https://docs.cloud.google.com/bigquery/docs/control-access-to-resources-iam).

## Import and analysis flow

1. Pick a connection type from the catalog (search and category filters).
2. Enter its details. Database and API connectors test the connection first; files and links go straight to reading their tables.
3. Select tables to import.
4. Import them into one immutable snapshot. Credentials are cleared from the page afterwards.
5. Review the proposed metric definitions and approve them. Ask only accepts approved snapshots (the API returns 409 for an unapproved one).

Each extraction creates one snapshot. Imports from different sources remain separate, and an Ask run selects one snapshot ID. There is no automatic cross-source join. Source IDs are namespaced by workspace so a source pointer cannot be reused to update a different workspace's record.

## Current maturity

The system supports read-only batch ingestion, schema discovery, table selection, source version history, and snapshot-pinned analysis. PostgreSQL and MySQL use repeatable-read transactions; uploads and links are static exports; Sheets, REST, Airtable and BigQuery use a best-effort extraction window. No adapter supports incremental extraction yet.

This is a controlled snapshot workflow, not a live-sync or CDC product. Refreshes are manual. The local app has no user login, so workspace scoping is logical organization and is not a multi-user security boundary.
