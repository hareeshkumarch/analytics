# Security Model

- **Loopback-Only Ingress**: Docker Compose binds `edge` (`127.0.0.1:8080`), `web` (`127.0.0.1:3000`), and `api` (`127.0.0.1:8000`) exclusively to loopback `127.0.0.1`. FastAPI rejects non-local `Host` headers and non-loopback bind attempts.
- **No `docker.sock` in Containers**: No service mounts `docker.sock`. (A sandboxed runner container was planned; its stub was removed until there is real work for it.)
- **SSRF & Destination Policy**: `destination_policy.py` blocks cloud metadata (`169.254.169.254`, `fd00:ec2::254`, `::ffff:169.254.169.254`) and link-local ranges unconditionally — local-test flags never override metadata/link-local denials. REST disables redirects and pins each connection to the validated IP while preserving the original Host header and TLS SNI.
- **SQL & DuckDB Isolation**: Models propose typed `QueryIR`, never raw SQL. `SqlGlotQueryCompiler` quotes catalog-resolved identifiers, parameterizes values, and runs full nested AST inspection. DuckDB opens staged snapshots `read_only=True` with `enable_external_access = false` and extensions locked.
- **SQLite Quarantine**: Copies uploads to an immutable `0o444` file, rejects live WAL/journal sidecars, disables `load_extension` and `trusted_schema`, enforces `sqlite3.set_authorizer`, and rejects views/triggers/virtual tables.
- **Encrypted Credentials**: `CredentialCipher` requires `APP_MASTER_KEY` (Fernet) and forbids plaintext persistence.
- **Export Safety**: CSV exports neutralize formula injection (`=`, `+`, `-`, `@`, `\t`, `\r`); HTML exports escape all text.
