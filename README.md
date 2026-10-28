# ClearWatt Utility Intelligence

A local-first, full-stack prototype for explainable electricity bill auditing and investigation support.

## Stack
- Frontend: React + TypeScript + Vite + Recharts
- API: FastAPI + pandas + NumPy
- Storage: SQLite (local persistent database)
- Analysis: explainable rules, robust MAD screening, data-quality checks, bill-component reconciliation
- Demo data is explicitly synthetic. Tariff schedules are not preconfigured or represented as official.

## Requirements
- Node.js 20+ and npm
- Python 3.10+

## Run locally (macOS)
Open two VS Code terminals in this folder.

### 1. Backend
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
API docs: http://localhost:8000/docs

### 2. Frontend
In a second terminal:
```bash
cd frontend
npm install
npm run dev
```
Open the URL printed by Vite (usually http://localhost:5173).

The frontend uses `/api` as a Vite proxy to `http://localhost:8000`.

## Included workflows
- Operations overview with live API-backed KPIs and consumption chart
- Investigation queue with status filtering and searchable case IDs
- Explainable case detail drawer with findings, caveats, status changes and notes
- CSV import with preview, validation and downloadable rejected-row report
- Consumer bill audit form
- Bill component arithmetic reconciliation (not tariff/legal correctness)
- Data-quality and methodology view
- CSV export of cases and JSON export of summary
- SQLite persistence for bills, cases, and case-history events
- Synthetic demo data seeded automatically on first run

## CSV import schema
The import screen accepts common column names, and maps them to:
`account_id,billing_start,billing_end,billed_units_kwh,bill_amount,previous_reading,current_reading,fixed_charge,energy_charge,adjustments,taxes,rebates,reading_method`
Required: `account_id`, `billing_start`, `billing_end`, `bill_amount`. Other fields are optional.
Dates should be ISO format (`YYYY-MM-DD`). Imported data is marked `user_imported`.

## Important limitations
- A flagged pattern is a screening signal, not proof of a billing error or faulty meter.
- Bill arithmetic checks only reconcile supplied components. They do not verify tariff legality or applicability.
- No official tariff rates are configured. The UI explicitly says tariff is not configured/verified.
- Demo records are fictional and labelled `synthetic_demo`.
- This is a local college prototype, not an official utility complaint portal.

## Smart-meter dataset dashboard (updated)
The Data Imports page now reads smart-meter counts from the separate `meter_readings` table. Imported readings are kept separate from synthetic billing records.

- **Smart-Meter Records**: paginated reading browser with meter-ID search, date filters, and source-file filtering. The API returns only the requested page, not the full dataset.
- **Overview**: includes a dedicated imported-smart-meter summary with record counts, unique meters, date coverage, and recent import status. The existing bill-audit metrics remain separate.
- **Analytics & quality**: includes dataset-backed monthly mean-measurement trends, top meters by reading count, and missing-measurement summaries, alongside the original bill-audit quality section.
- New backend endpoints: `GET /api/dataset/analytics`; enhanced `GET /api/dataset/readings` supports `limit`, `offset`, `meter_id` substring search, `start_date`, `end_date`, and `source_filename` filters and returns a `total` count.

**Interpretation caution:** the dataset importer maps common source columns into a generic `consumption` field. The UI therefore labels the chart as a *reported measurement* and shows its mean rather than assuming the values are kWh or summing them. Confirm the source documentation and the exact measurement column semantics before presenting energy totals or model accuracy.

After importing a dataset, keep the import page open until the job completes. The frontend refreshes the overview after completion. To see the readings, open **Smart-Meter Records**; to see aggregates, open **Analytics & quality**.

## Recent dashboard improvements

The Analytics & quality page now shows more descriptive views of imported smart-meter data: monthly mean measurement, monthly row counts, a daily mean trend for the latest 90 days, top meters by number of readings, and a measurement-value distribution. It also reports missing, zero, and negative values where available. These are descriptive checks only. Verify the source unit and semantics before interpreting values as kWh or claiming an anomaly is a fault.

The Overview **Export summary** action now downloads `clearwatt-summary-report.html`, a readable report with import counts, dataset coverage, bill/case totals, and limitations. Open it in a browser and use **Print / Save as PDF** if you need a PDF. The JSON endpoint remains available for integrations.

### Important: protect your imported dataset

- Imported readings are stored in `backend/clearwatt.db` on the machine where the backend runs.
- Updating source code does not automatically delete that database, but extracting a ZIP over files is not a backup strategy. Before replacing folders, make a copy of `backend/clearwatt.db` while the backend is stopped.
- This source ZIP contains the project's database backup file, not the newest 4.5-million-row database from your Mac. The imported data on your Mac is not embedded in this ZIP, so it cannot be carried to another computer by downloading this code ZIP alone.
- Keep the same `backend/clearwatt.db` file in place to retain your local imports. Do not delete it or run database-reset commands unless you intend to erase the records.
