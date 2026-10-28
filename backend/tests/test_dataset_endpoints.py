"""Smoke test the dataset API functions using an isolated temporary SQLite DB.
Run from backend/: python tests/test_dataset_endpoints.py
"""
import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main

main.DB_PATH = Path(tempfile.mkdtemp()) / "clearwatt-test.db"
con = main.connect()
rows = [
    ("testhash", "sample.csv", "METER-A", "2021-01-01T00:00:00", 1.5, 230.0, 2.1, 50.0, "{}", "2021-01-01T00:01:00Z"),
    ("testhash", "sample.csv", "METER-B", "2021-01-02T00:00:00", 2.5, 231.0, 2.2, 50.0, "{}", "2021-01-02T00:01:00Z"),
    ("testhash", "sample.csv", "METER-A", "2021-02-01T00:00:00", None, None, None, None, "{}", "2021-02-01T00:01:00Z"),
]
con.executemany("INSERT INTO meter_readings(source_hash,source_filename,meter_id,timestamp,consumption,voltage,current,frequency,raw_json,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
con.commit(); con.close()

summary = main.dataset_overview()
assert summary["total_readings"] == 3, summary
assert summary["unique_meters"] == 2, summary
assert summary["missing_consumption"] == 1, summary
page = main.dataset_readings(limit=1, offset=0, meter_id="METER-A", start_date="2021-01-01", end_date="2021-02-01")
assert page["total"] == 2 and len(page["items"]) == 1 and page["has_more"] is True, page
analytics = main.dataset_analytics()
assert len(analytics["monthly"]) == 2, analytics
assert analytics["quality"]["total"] == 3, analytics
assert analytics["top_meters"][0]["meter_id"] == "METER-A", analytics
print("PASS: dataset overview, pagination/filtering, missing-value counts, and analytics aggregates")
