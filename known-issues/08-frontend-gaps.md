# 08 - Frontend and API gaps (superseded)

This file described the UI before the Data page rebuild. Its rows are resolved or tracked in
[12](12-open-issues-reasons-and-fixes.md):

- Data page (catalog, connect, import, Definitions/Profile/Preview/Versions/Diagnostics tabs): rebuilt
  on 2026-11-24 after a `.gitignore` rule had kept it out of git.
- Snapshot versions: allocated on the server (#8), so re-imports link up across sessions.
- Uploads: sent once through `POST /uploads` (#23).
- Deleting datasets and runs: #37.
- Still open for the UI: follow-up questions (#31), URL state for tabs and filters (#32),
  accessibility gaps (#35), charts (I1), export (I2) and frontend unit tests (I9).
