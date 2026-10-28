# ClearWatt smart-meter dashboard integration changes

## What changed

- Added `GET /api/dataset/analytics` for database-side monthly, daily, meter-level and missing-value aggregates.
- Enhanced `GET /api/dataset/readings` with server-side meter substring search, date filters, source-file filter, total result count, bounded pagination and `has_more` metadata.
- Added a Smart-Meter Records page with paginated rows, meter/date/source filters and actual source filenames.
- Added imported smart-meter metrics and import provenance to Overview.
- Added smart-meter measurement analytics to Analytics & quality while preserving the separate bill-audit analytics.
- Updated the Data Imports summary to distinguish smart-meter readings, synthetic demo bills, unique meters and completed imports.
- Added a backend smoke test for counts, filtering, pagination and aggregates.

## Validation performed

- `python -m py_compile backend/app/main.py` — passed.
- `python backend/tests/test_dataset_endpoints.py` — passed against an isolated temporary SQLite database with sample readings.
- TypeScript compiler was invoked against `frontend/src/App.tsx`; it reported missing external modules (`react`, `lucide-react`, `recharts`) because dependencies could not be installed in this environment. It reported no TSX syntax diagnostics. A full frontend production build could not be verified here without package-registry access.

## Important

The archive contains the modified source code, not the user's live Vercel/v0 database or its 550,560 imported rows. The existing deployed project will not change merely by downloading this archive. To deploy these edits, the source must be synced into the connected Git repository or the updated project must be run/deployed from this archive. Re-import the dataset into the target database if that environment does not already contain it.

The source column mapped to `consumption` has not been independently verified as interval kWh, instantaneous power, or cumulative reading. Charts therefore use mean reported measurements and avoid claiming total energy consumption until units/semantics are verified.

## Analytics and export improvements (28 Oct 2026)

- Expanded the Analytics & quality view with monthly average measurement, monthly row coverage, daily mean trend (latest 90 days), top meters by row count, and an eight-bin measurement distribution.
- Added descriptive quality fields for zero/negative measurements and min/max/mean. These remain source-unit statistics; they are not treated as confirmed faults or automatically converted to kWh.
- Added a readable HTML summary export that includes smart-meter counts, date coverage, import outcomes, bill/case counts, and explicit limitations. It can be printed or saved as PDF from the browser.
- The previous JSON summary endpoint remains available for programmatic use.
- Validation: Python backend syntax check passed. Frontend production build could not be completed in this environment because dependency installation timed out; run `npm install` and `npm run build` locally before presenting.
