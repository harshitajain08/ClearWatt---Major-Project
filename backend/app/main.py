from __future__ import annotations
from fastapi import BackgroundTasks, FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from pathlib import Path
import sqlite3, csv, io, json, uuid, hashlib, zipfile, math, tempfile, os
from datetime import datetime, date, timedelta
from .analysis import analyse_bills, audit_bill, quality_report

DB_PATH = Path(__file__).resolve().parent.parent / "clearwatt.db"
app = FastAPI(title="ClearWatt Utility Intelligence API", version="1.0.0",
              description="Explainable bill screening prototype. Flags are not proof of billing error.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173","http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class BillIn(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    billing_start: str
    billing_end: str
    billed_units_kwh: Optional[float] = None
    bill_amount: float = Field(ge=-10000000, le=10000000)
    previous_reading: Optional[float] = None
    current_reading: Optional[float] = None
    fixed_charge: Optional[float] = None
    energy_charge: Optional[float] = None
    adjustments: Optional[float] = 0
    taxes: Optional[float] = 0
    rebates: Optional[float] = 0
    reading_method: Optional[str] = "unknown"
    provenance_type: str = "user_imported"

class StatusIn(BaseModel):
    status: str
    note: str = ""
    reviewer: str = "Demo reviewer"

ALLOWED_STATUSES = {"New","Under review","Needs information","Verified discrepancy","No discrepancy found","Resolved"}
def connect():
    con=sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("""CREATE TABLE IF NOT EXISTS bills (
      bill_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, billing_start TEXT NOT NULL, billing_end TEXT NOT NULL,
      billed_units_kwh REAL, bill_amount REAL NOT NULL, previous_reading REAL, current_reading REAL,
      fixed_charge REAL, energy_charge REAL, adjustments REAL DEFAULT 0, taxes REAL DEFAULT 0, rebates REAL DEFAULT 0,
      reading_method TEXT, provenance_type TEXT NOT NULL DEFAULT 'user_imported', created_at TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS cases (
      case_id TEXT PRIMARY KEY, bill_id TEXT NOT NULL, status TEXT NOT NULL, priority TEXT NOT NULL,
      score INTEGER NOT NULL, signals_json TEXT NOT NULL, bill_audit_json TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, owner TEXT DEFAULT 'Unassigned', FOREIGN KEY(bill_id) REFERENCES bills(bill_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS case_history (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL,
      reviewer TEXT NOT NULL, created_at TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS meter_readings (
      reading_id INTEGER PRIMARY KEY AUTOINCREMENT, source_hash TEXT NOT NULL, source_filename TEXT NOT NULL,
      meter_id TEXT, timestamp TEXT, consumption REAL, voltage REAL, current REAL, frequency REAL,
      raw_json TEXT NOT NULL, imported_at TEXT NOT NULL, UNIQUE(source_hash, meter_id, timestamp))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_meter_readings_meter_time ON meter_readings(meter_id, timestamp)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_meter_readings_timestamp ON meter_readings(timestamp)")
    con.execute("""CREATE TABLE IF NOT EXISTS dataset_imports (
      import_id TEXT PRIMARY KEY, source_filename TEXT NOT NULL, source_hash TEXT NOT NULL UNIQUE,
      imported_at TEXT NOT NULL, rows_received INTEGER NOT NULL, rows_accepted INTEGER NOT NULL,
      rows_rejected INTEGER NOT NULL, rows_deduplicated INTEGER NOT NULL, status TEXT NOT NULL, errors_json TEXT NOT NULL)""")
    con.commit()
    if con.execute("SELECT COUNT(*) FROM bills").fetchone()[0] == 0: seed(con)
    return con
def seed(con):
    now=datetime.utcnow().isoformat(timespec="seconds")+"Z"
    # Fictional, varied demo data; not real consumer records or official tariff calculations.
    rows=[]
    for a in range(1,11):
        base=120 + (a*19)%170
        for p in range(1,5):
            units=round(base*(0.88 + ((a+p)%5)*0.07),1)
            if a==3 and p==4: units=units*1.9
            if a==7 and p==4: units=-12
            amount=round(90+units*6.2,2)
            if a==5 and p==4: amount+=125
            start=date(2026,1,1)+timedelta(days=(p-1)*30)
            end=start+timedelta(days=29)
            fixed=90.0; energy=round(units*6.2,2) if units>=0 else 0.0
            # Make some component sets intentionally incomplete; arithmetic isn't inferred from tariffs.
            adj=0.0; tax=0.0; rebate=0.0
            bid=f"DEMO-{a:02d}-{p:02d}"
            rows.append((bid,f"SYN-{a:03d}",start.isoformat(),end.isoformat(),units,amount,None,None,fixed,energy,adj,tax,rebate,"actual","synthetic_demo",now))
    con.executemany("""INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",rows)
    con.commit()
    bills=[dict(zip(["bill_id","account_id","billing_start","billing_end","billed_units_kwh","bill_amount","previous_reading","current_reading","fixed_charge","energy_charge","adjustments","taxes","rebates","reading_method","provenance_type","created_at"],r)) for r in rows]
    findings=analyse_bills(bills)
    for b,f in zip(bills,findings):
        if f["signals"]:
            cid="CW-"+uuid.uuid4().hex[:8].upper()
            status="Under review" if f["priority"]=="High" else "New"
            con.execute(
                """INSERT INTO cases (
                    case_id, bill_id, status, priority, score,
                    signals_json, bill_audit_json, created_at,
                    updated_at, owner
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    b["bill_id"],
                    status,
                    f["priority"],
                    f["score"],
                    json.dumps(f["signals"]),
                    json.dumps(f["bill_audit"]),
                    now,
                    now,
                    "Unassigned",
                ),
            )
            con.execute("INSERT INTO case_history(case_id,status,note,reviewer,created_at) VALUES (?,?,?,?,?)",(cid,status,"Seeded synthetic demonstration case","Demo reviewer",now))
    con.commit()
def all_bills(con):
    return [dict(r) for r in con.execute("SELECT * FROM bills ORDER BY billing_end, account_id")]
def all_cases(con):
    rows=con.execute("""SELECT c.*,b.account_id,b.billing_start,b.billing_end,b.bill_amount,b.billed_units_kwh,b.provenance_type
                        FROM cases c JOIN bills b ON b.bill_id=c.bill_id ORDER BY
                        CASE c.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, c.created_at DESC""").fetchall()
    out=[]
    for r in rows:
        d=dict(r); d["signals"]=json.loads(d.pop("signals_json")); d["bill_audit"]=json.loads(d.pop("bill_audit_json"))
        out.append(d)
    return out
@app.get("/api/health")
def health(): return {"status":"ok","service":"ClearWatt API","database":"SQLite local persistence","workflow":"n8n-compatible bill audit webhook"}

@app.post("/api/workflows/bill-audit")
async def workflow_bill_audit(data: UploadFile = File(...)):
    """n8n-compatible PDF intake endpoint for local preview and automation.

    Text-based PDFs are parsed without external services; image-only PDFs return
    a clear OCR-required response. The same deterministic audit rules power the
    dashboard and the imported n8n workflow.
    """
    if not data.filename or not data.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF bill")
    raw = await data.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "PDF must be 10 MB or smaller")
    text = raw.decode("latin-1", errors="ignore")
    import re
    def value(pattern, default=None):
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).replace(",", "").strip() if match else default
    account_id = value(r"(?:account[_ ]?id|account number|consumer number)\\s*[:#-]?\\s*([A-Z0-9-]+)", "PDF-REVIEW")
    start = value(r"(?:billing[_ ]?start|from|start date)\\s*[:#-]?\\s*(\\d{4}-\\d{2}-\\d{2})")
    end = value(r"(?:billing[_ ]?end|to|end date)\\s*[:#-]?\\s*(\\d{4}-\\d{2}-\\d{2})")
    amount = value(r"(?:bill[_ ]?amount|total(?: amount)?|amount due)\\s*[:₹$-]?\\s*([0-9,]+(?:\\.[0-9]+)?)")
    units = value(r"(?:billed[_ ]?units|consumption|units)\\s*[:=-]?\\s*([0-9,]+(?:\\.[0-9]+)?)")
    if not start or not end or amount is None:
        raise HTTPException(422, "Could not extract billing_start, billing_end and bill_amount from this PDF. Text-based PDF required; scanned PDFs need OCR.")
    payload = BillIn(account_id=account_id, billing_start=start, billing_end=end,
                     billed_units_kwh=float(units) if units else None, bill_amount=float(amount),
                     provenance_type="workflow_pdf")
    result = create_audit(payload)
    return {"source":"n8n-compatible webhook", "filename":data.filename, "extracted_text_preview":text[:500], "audit":result}
def _pick_column(columns, candidates):
    normalized = {''.join(ch.lower() for ch in c if ch.isalnum()): c for c in columns}
    for candidate in candidates:
        key = ''.join(ch.lower() for ch in candidate if ch.isalnum())
        if key in normalized: return normalized[key]
    for c in columns:
        key = ''.join(ch.lower() for ch in c if ch.isalnum())
        if any(token in key for token in candidates): return c
    return None

def _ingest_reading_rows(con, filename, source_hash, rows, received, errors):
    now=datetime.utcnow().isoformat(timespec="seconds")+"Z"; accepted=dedup=0; params=[]
    for raw in rows:
        try:
            meter_col=_pick_column(raw.keys(), ["meter_id","meter","device_id","account_id"])
            time_col=_pick_column(raw.keys(), ["timestamp","datetime","date_time","reading_time"])
            consumption_col=_pick_column(raw.keys(), ["consumption","energy","energy_kwh","kwh","load","power"])
            if not meter_col or not time_col: raise ValueError("Could not map meter identifier and timestamp columns")
            stamp=str(raw.get(time_col) or '').strip(); parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            meter=str(raw.get(meter_col) or '').strip()
            if not meter: raise ValueError("Missing meter identifier")
            def number(c):
                if not c or raw.get(c) in (None,''): return None
                value=float(str(raw[c]).replace(',',''))
                if not math.isfinite(value): raise ValueError(f"Invalid numeric value in {c}")
                return value
            params.append((source_hash,filename,meter,parsed.isoformat(),number(consumption_col),number(_pick_column(raw.keys(),["voltage","volt"])),number(_pick_column(raw.keys(),["current","ampere"])),number(_pick_column(raw.keys(),["frequency","hz"])),json.dumps(raw,ensure_ascii=False),now))
        except Exception as exc: errors.append({"reason":str(exc),"row":raw})
    before=con.total_changes
    con.executemany("INSERT OR IGNORE INTO meter_readings(source_hash,source_filename,meter_id,timestamp,consumption,voltage,current,frequency,raw_json,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)",params)
    inserted=con.total_changes-before
    accepted=inserted; dedup=len(params)-inserted
    con.commit(); return accepted,dedup

def _process_dataset_import(import_id, filename, source_hash, temp_path):
    con=connect(); errors=[]; received=accepted=dedup=0
    try:
        def process_stream(name, stream):
            nonlocal received, accepted, dedup
            reader=csv.DictReader(stream)
            if not reader.fieldnames: raise ValueError("CSV has no header row")
            batch=[]
            for row in reader:
                batch.append(row); received+=1
                if len(batch)>=50000:
                    a,d=_ingest_reading_rows(con,name,source_hash,batch,received,errors); accepted+=a; dedup+=d; batch.clear()
                    con.execute("UPDATE dataset_imports SET rows_received=?,rows_accepted=?,rows_rejected=?,rows_deduplicated=? WHERE import_id=?",(received,accepted,len(errors),dedup,import_id)); con.commit()
            if batch:
                a,d=_ingest_reading_rows(con,name,source_hash,batch,received,errors); accepted+=a; dedup+=d
        with open(temp_path,"rb") as source:
            if filename.lower().endswith(".zip"):
                with zipfile.ZipFile(source) as archive:
                    csv_infos=[info for info in archive.infolist() if info.filename.lower().endswith(".csv")]
                    if not csv_infos: raise ValueError("ZIP contains no CSV files")
                    for info in csv_infos:
                        with archive.open(info) as raw_stream, io.TextIOWrapper(raw_stream,encoding="utf-8-sig",errors="replace") as text_stream: process_stream(info.filename,text_stream)
            else:
                with io.TextIOWrapper(source,encoding="utf-8-sig",errors="replace") as text_stream: process_stream(filename,text_stream)
        con.execute("UPDATE dataset_imports SET rows_received=?,rows_accepted=?,rows_rejected=?,rows_deduplicated=?,status=?,errors_json=? WHERE import_id=?",(received,accepted,len(errors),dedup,"completed",json.dumps(errors[:100],ensure_ascii=False),import_id)); con.commit()
    except Exception as exc:
        con.execute("UPDATE dataset_imports SET rows_received=?,rows_accepted=?,rows_rejected=?,rows_deduplicated=?,status=?,errors_json=? WHERE import_id=?",(received,accepted,len(errors),dedup,"failed",json.dumps(errors[:100]+[{"reason":str(exc)}],ensure_ascii=False),import_id)); con.commit()
    finally:
        con.close(); Path(temp_path).unlink(missing_ok=True)

@app.post("/api/import/dataset", status_code=202)
async def import_dataset(background_tasks: BackgroundTasks, file: UploadFile=File(...)):
    """Stream the upload to disk and process it after returning a fast job response.

    This avoids proxy execution-time limits for large files while keeping parsing and
    database writes bounded; the browser polls the returned import_id for progress.
    """
    filename=file.filename or "dataset"
    if not filename.lower().endswith((".csv", ".zip")):
        raise HTTPException(400,"Upload a CSV or ZIP containing CSV files")
    import_id=uuid.uuid4().hex; temp_path=None
    try:
        with tempfile.NamedTemporaryFile(prefix="clearwatt-", suffix=Path(filename).suffix, delete=False) as temp:
            temp_path=temp.name; digest=hashlib.sha256(); size=0
            while chunk:=await file.read(1024*1024):
                size += len(chunk)
                if size > 2 * 1024 * 1024 * 1024: raise HTTPException(413,"Dataset exceeds the 2 GB streaming upload limit")
                digest.update(chunk); temp.write(chunk)
        source_hash=digest.hexdigest(); con=connect()
        existing=con.execute("SELECT import_id,status FROM dataset_imports WHERE source_hash=?",(source_hash,)).fetchone()
        if existing and existing["status"] in ("completed","processing"):
            Path(temp_path).unlink(missing_ok=True); con.close()
            return {"status":"already_imported" if existing["status"]=="completed" else "processing","import_id":existing["import_id"],"source_filename":filename,"source_hash":source_hash}
        con.execute("INSERT OR REPLACE INTO dataset_imports(import_id,source_filename,source_hash,imported_at,rows_received,rows_accepted,rows_rejected,rows_deduplicated,status,errors_json) VALUES (?,?,?,?,?,?,?,?,?,?)",(import_id,filename,source_hash,datetime.utcnow().isoformat()+"Z",0,0,0,0,"processing",json.dumps([]))); con.commit(); con.close()
        background_tasks.add_task(_process_dataset_import,import_id,filename,source_hash,temp_path); temp_path=None
        return {"status":"processing","import_id":import_id,"source_filename":filename,"source_hash":source_hash}
    finally:
        await file.close()
        if temp_path: Path(temp_path).unlink(missing_ok=True)

@app.get("/api/imports/{import_id}")
def import_status(import_id: str):
    con=connect()
    try:
        row=con.execute("SELECT * FROM dataset_imports WHERE import_id=?",(import_id,)).fetchone()
        if not row: raise HTTPException(404,"Import not found")
        result=dict(row)
        result["errors"]=json.loads(result.pop("errors_json") or "[]")
        return result
    finally: con.close()

@app.get("/api/dataset/overview")
def dataset_overview():
    con=connect()
    try:
        stats=con.execute("SELECT COUNT(*) total, COUNT(DISTINCT meter_id) meters, MIN(timestamp) earliest, MAX(timestamp) latest, SUM(CASE WHEN consumption IS NULL THEN 1 ELSE 0 END) missing FROM meter_readings").fetchone()
        peak=con.execute("SELECT meter_id,timestamp,consumption FROM meter_readings WHERE consumption IS NOT NULL ORDER BY consumption DESC LIMIT 1").fetchone()
        return {"total_readings":stats[0],"unique_meters":stats[1],"earliest":stats[2],"latest":stats[3],"missing_consumption":stats[4],"peak":dict(peak) if peak else None,"imports":[dict(r) for r in con.execute("SELECT source_filename,imported_at,rows_received,rows_accepted,rows_rejected,rows_deduplicated,status FROM dataset_imports ORDER BY imported_at DESC")]}
    finally: con.close()

@app.get("/api/dataset/readings")
def dataset_readings(limit:int=100, offset:int=0, meter_id:Optional[str]=None,
                     start_date:Optional[str]=None, end_date:Optional[str]=None,
                     source_filename:Optional[str]=None):
    """Paginated access to real imported readings; never returns the full dataset."""
    limit=max(1,min(limit,200)); offset=max(0,offset); con=connect()
    try:
        clauses=[]; params=[]
        if meter_id and meter_id.strip(): clauses.append("meter_id LIKE ?"); params.append(f"%{meter_id.strip()}%")
        if start_date: clauses.append("substr(timestamp,1,10) >= ?"); params.append(start_date)
        if end_date: clauses.append("substr(timestamp,1,10) <= ?"); params.append(end_date)
        if source_filename and source_filename.strip(): clauses.append("source_filename = ?"); params.append(source_filename.strip())
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        total=con.execute(f"SELECT COUNT(*) FROM meter_readings{where}",params).fetchone()[0]
        rows=con.execute(f"SELECT reading_id,meter_id,timestamp,consumption,voltage,current,frequency,source_filename FROM meter_readings{where} ORDER BY timestamp,meter_id LIMIT ? OFFSET ?",(*params,limit,offset)).fetchall()
        return {"items":[dict(r) for r in rows],"total":total,"limit":limit,"offset":offset,"has_more":offset+len(rows)<total}
    finally: con.close()

@app.get("/api/dataset/analytics")
def dataset_analytics(start_date:Optional[str]=None, end_date:Optional[str]=None, meter_id:Optional[str]=None):
    """Efficient, source-aware aggregates for the smart-meter dashboard.

    The source's measurement semantics may vary, so consumption is presented as
    the mean reported measurement in source units, not automatically summed as kWh.
    """
    con=connect()
    try:
        clauses=[]; params=[]
        if start_date: clauses.append("substr(timestamp,1,10) >= ?"); params.append(start_date)
        if end_date: clauses.append("substr(timestamp,1,10) <= ?"); params.append(end_date)
        if meter_id and meter_id.strip(): clauses.append("meter_id LIKE ?"); params.append(f"%{meter_id.strip()}%")
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        monthly=[dict(r) for r in con.execute(f"SELECT substr(timestamp,1,7) period, COUNT(*) readings, COUNT(DISTINCT meter_id) meters, AVG(consumption) avg_measurement, MIN(consumption) min_measurement, MAX(consumption) max_measurement FROM meter_readings{where} GROUP BY substr(timestamp,1,7) ORDER BY period",params)]
        daily=[dict(r) for r in con.execute(f"SELECT substr(timestamp,1,10) period, COUNT(*) readings, AVG(consumption) avg_measurement, MIN(consumption) min_measurement, MAX(consumption) max_measurement FROM meter_readings{where} GROUP BY substr(timestamp,1,10) ORDER BY period DESC LIMIT 90",params)]
        meters=[dict(r) for r in con.execute(f"SELECT meter_id, COUNT(*) readings, AVG(consumption) avg_measurement, MIN(consumption) min_measurement, MAX(consumption) max_measurement, MIN(timestamp) first_seen, MAX(timestamp) last_seen FROM meter_readings{where} GROUP BY meter_id ORDER BY readings DESC LIMIT 20",params)]
        quality=con.execute(f"SELECT COUNT(*) total, COUNT(DISTINCT meter_id) meters, SUM(CASE WHEN consumption IS NULL THEN 1 ELSE 0 END) missing_consumption, SUM(CASE WHEN voltage IS NULL THEN 1 ELSE 0 END) missing_voltage, SUM(CASE WHEN current IS NULL THEN 1 ELSE 0 END) missing_current, SUM(CASE WHEN consumption=0 THEN 1 ELSE 0 END) zero_consumption, SUM(CASE WHEN consumption<0 THEN 1 ELSE 0 END) negative_consumption, MIN(consumption) min_measurement, MAX(consumption) max_measurement, AVG(consumption) avg_measurement FROM meter_readings{where}",params).fetchone()
        q=dict(quality)
        distribution=[]
        if q.get("min_measurement") is not None and q.get("max_measurement") is not None:
            lo=float(q["min_measurement"]); hi=float(q["max_measurement"]); width=(hi-lo)/8 if hi>lo else 1.0
            case_sql=" ".join([f"WHEN consumption < ? THEN {i-1}" for i in range(1,8)])
            bounds=[lo+width*i for i in range(1,8)]
            filtered_where=(where + " AND " if where else " WHERE ") + "consumption IS NOT NULL"
            rows=con.execute(f"SELECT bucket, COUNT(*) count FROM (SELECT CASE {case_sql} ELSE 7 END bucket FROM meter_readings{filtered_where}) GROUP BY bucket ORDER BY bucket",[*bounds,*params]).fetchall()
            counts={int(r["bucket"]):int(r["count"]) for r in rows}
            distribution=[{"range":f"{lo+width*i:.4g}–{lo+width*(i+1):.4g}" if hi>lo else f"{lo:.4g}","count":counts.get(i,0)} for i in range(8)]
        return {"monthly":monthly,"daily":list(reversed(daily)),"top_meters":meters,"distribution":distribution,"quality":q,"measurement_note":"Descriptive statistics are calculated from imported rows. Values remain in source units; verify whether the column represents interval energy, instantaneous power or cumulative readings before interpreting sums as kWh. Zero and negative values can be source-specific states and are not automatically faults."}
    finally: con.close()

@app.get("/api/overview")
def overview():
    con=connect()
    try:
        bills=all_bills(con); cases=all_cases(con)
        return {"records_processed":len(bills),"meters_represented":len(set(b["account_id"] for b in bills)),
                "open_cases":sum(c["status"] not in ("Resolved","No discrepancy found") for c in cases),
                "high_priority":sum(c["priority"]=="High" and c["status"] not in ("Resolved","No discrepancy found") for c in cases),
                "data_quality_issues":sum(b["billed_units_kwh"] is None for b in bills),
                "verified_discrepancies":sum(c["status"]=="Verified discrepancy" for c in cases),
                "bills":bills,"cases":cases,"synthetic_records":sum(b["provenance_type"]=="synthetic_demo" for b in bills)}
    finally: con.close()
@app.get("/api/bills")
def get_bills():
    con=connect()
    try: return all_bills(con)
    finally: con.close()
@app.get("/api/cases")
def get_cases(status: Optional[str]=None, q: Optional[str]=None):
    con=connect()
    try:
        cases=all_cases(con)
        if status and status!="All": cases=[c for c in cases if c["status"]==status]
        if q: cases=[c for c in cases if q.lower() in (c["case_id"]+c["bill_id"]+c["account_id"]).lower()]
        return cases
    finally: con.close()
@app.get("/api/cases/{case_id}")
def case_detail(case_id: str):
    con=connect()
    try:
        c=next((x for x in all_cases(con) if x["case_id"]==case_id),None)
        if not c: raise HTTPException(404,"Case not found")
        c["bill"]=dict(con.execute("SELECT * FROM bills WHERE bill_id=?",(c["bill_id"],)).fetchone())
        c["history"]=[dict(r) for r in con.execute("SELECT * FROM case_history WHERE case_id=? ORDER BY created_at",(case_id,))]
        return c
    finally: con.close()
@app.patch("/api/cases/{case_id}/status")
def update_status(case_id: str, payload: StatusIn):
    if payload.status not in ALLOWED_STATUSES: raise HTTPException(400,"Unsupported case status")
    con=connect()
    try:
        exists=con.execute("SELECT case_id FROM cases WHERE case_id=?",(case_id,)).fetchone()
        if not exists: raise HTTPException(404,"Case not found")
        now=datetime.utcnow().isoformat(timespec="seconds")+"Z"
        con.execute("UPDATE cases SET status=?,updated_at=? WHERE case_id=?",(payload.status,now,case_id))
        con.execute("INSERT INTO case_history(case_id,status,note,reviewer,created_at) VALUES (?,?,?,?,?)",(case_id,payload.status,payload.note,payload.reviewer,now))
        con.commit()
        return {"ok":True,"case_id":case_id,"status":payload.status}
    finally: con.close()
@app.post("/api/audit")
def create_audit(payload: BillIn):
    data=payload.model_dump()
    try:
        date.fromisoformat(data["billing_start"]); date.fromisoformat(data["billing_end"])
    except ValueError: raise HTTPException(400,"Billing dates must use YYYY-MM-DD")
    if data["billing_end"] < data["billing_start"]: raise HTTPException(400,"Billing end must be on or after billing start")
    data["bill_id"]="USR-"+uuid.uuid4().hex[:10].upper()
    data["created_at"]=datetime.utcnow().isoformat(timespec="seconds")+"Z"
    con=connect()
    try:
        con.execute("""INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",tuple(data[k] for k in ["bill_id","account_id","billing_start","billing_end","billed_units_kwh","bill_amount","previous_reading","current_reading","fixed_charge","energy_charge","adjustments","taxes","rebates","reading_method","provenance_type","created_at"]))
        # Compare this bill against existing account history (the current record is included but excluded by analysis history selection).
        bills=all_bills(con); f=next(x for x in analyse_bills(bills) if x["source_id"]==data["bill_id"])
        cid="CW-"+uuid.uuid4().hex[:8].upper(); now=data["created_at"]
        if f["signals"]:
            con.execute("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?)",(cid,data["bill_id"],"New",f["priority"],f["score"],json.dumps(f["signals"]),json.dumps(f["bill_audit"]),now,now,"Unassigned"))
            con.execute("INSERT INTO case_history(case_id,status,note,reviewer,created_at) VALUES (?,?,?,?,?)",(cid,"New","Consumer audit submitted","System",now))
        con.commit()
        return {"bill":data,"case_id":cid if f["signals"] else None,"findings":f["signals"],"score":f["score"],"priority":f["priority"],
                "bill_audit":f["bill_audit"],"message":"Screening completed. Findings are investigation signals, not a verdict that the bill is wrong."}
    finally: con.close()
@app.post("/api/import/csv")
async def import_csv(file: UploadFile=File(...)):
    if not file.filename.lower().endswith(".csv"): raise HTTPException(400,"Please upload a CSV file")
    raw=await file.read()
    if len(raw)>10*1024*1024: raise HTTPException(413,"CSV must be 10 MB or smaller")
    try: reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    except UnicodeDecodeError: raise HTTPException(400,"CSV must be UTF-8 encoded")
    if not reader.fieldnames: raise HTTPException(400,"CSV has no header row")
    aliases={"account":"account_id","accountid":"account_id","customer_id":"account_id","units":"billed_units_kwh","kwh":"billed_units_kwh","consumption":"billed_units_kwh","amount":"bill_amount","total":"bill_amount","start_date":"billing_start","end_date":"billing_end","bill_date_start":"billing_start","bill_date_end":"billing_end"}
    def norm(s): return "".join(ch.lower() for ch in s.strip() if ch.isalnum() or ch=="_")
    rows=[]; rejected=[]
    for n,original in enumerate(reader, start=2):
        row={}
        for k,v in original.items():
            key=norm(k or ""); key=aliases.get(key,key)
            row[key]=(v or "").strip()
        try:
            for required in ("account_id","billing_start","billing_end","bill_amount"):
                if not row.get(required): raise ValueError(f"Missing required field: {required}")
            date.fromisoformat(row["billing_start"]); date.fromisoformat(row["billing_end"])
            def num(k):
                if not row.get(k): return None
                return float(row[k].replace("₹","").replace(",",""))
            item={"account_id":row["account_id"],"billing_start":row["billing_start"],"billing_end":row["billing_end"],
              "billed_units_kwh":num("billed_units_kwh"),"bill_amount":num("bill_amount"),"previous_reading":num("previous_reading"),
              "current_reading":num("current_reading"),"fixed_charge":num("fixed_charge"),"energy_charge":num("energy_charge"),
              "adjustments":num("adjustments") or 0,"taxes":num("taxes") or 0,"rebates":num("rebates") or 0,
              "reading_method":row.get("reading_method") or "unknown","provenance_type":"user_imported"}
            if item["billing_end"]<item["billing_start"]: raise ValueError("Billing end precedes start")
            rows.append((n,item))
        except Exception as e: rejected.append({"row":n,"reason":str(e),"data":original})
    con=connect(); imported=0; imported_bills=[]
    try:
        now=datetime.utcnow().isoformat(timespec="seconds")+"Z"
        for n,item in rows:
            item["bill_id"]="IMP-"+uuid.uuid4().hex[:10].upper(); item["created_at"]=now
            try:
                con.execute("INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",tuple(item[k] for k in ["bill_id","account_id","billing_start","billing_end","billed_units_kwh","bill_amount","previous_reading","current_reading","fixed_charge","energy_charge","adjustments","taxes","rebates","reading_method","provenance_type","created_at"]))
                imported+=1
                imported_bills.append(item.copy())
            except Exception as e: rejected.append({"row":n,"reason":str(e),"data":{}})
        con.commit()
        # Screen only this upload. Re-scanning the entire database makes imports slow
        # and can block the request when the workspace contains many records.
        for b,f in zip(imported_bills,analyse_bills(imported_bills)):
            if f["signals"]:
                cid="CW-"+uuid.uuid4().hex[:8].upper()
                con.execute("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?)",(cid,b["bill_id"],"New",f["priority"],f["score"],json.dumps(f["signals"]),json.dumps(f["bill_audit"]),now,now,"Unassigned"))
                con.execute("INSERT INTO case_history(case_id,status,note,reviewer,created_at) VALUES (?,?,?,?,?)",(cid,"New","Created from CSV import","System",now))
        con.commit()
        return {"filename":file.filename,"rows_seen":len(rows)+len(rejected),"imported":imported,"rejected":len(rejected),"rejected_rows":rejected,"quality":quality_report(imported_bills)}
    finally: con.close()
@app.get("/api/quality")
def quality():
    con=connect()
    try:
        bills=all_bills(con); return {"report":quality_report(bills),"provenance_counts":{k:sum(b["provenance_type"]==k for b in bills) for k in set(b["provenance_type"] for b in bills)}}
    finally: con.close()
@app.get("/api/export/cases.csv")
def export_cases():
    from fastapi.responses import StreamingResponse
    con=connect()
    try:
        cases=all_cases(con); buf=io.StringIO(); cols=["case_id","bill_id","account_id","status","priority","score","billing_start","billing_end","billed_units_kwh","bill_amount","provenance_type"]
        w=csv.DictWriter(buf,fieldnames=cols); w.writeheader()
        for c in cases: w.writerow({k:c.get(k) for k in cols})
        return StreamingResponse(iter([buf.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=clearwatt-cases.csv"})
    finally: con.close()
@app.get("/api/export/summary.json")
def export_summary():
    con=connect()
    try:
        bills=all_bills(con); cases=all_cases(con)
        return {"generated_at":datetime.utcnow().isoformat()+"Z","prototype":"ClearWatt Utility Intelligence",
            "records_processed":len(bills),"cases_total":len(cases),"cases_reviewed":sum(c["status"]!="New" for c in cases),
            "verified_discrepancies":sum(c["status"]=="Verified discrepancy" for c in cases),
            "synthetic_records":sum(b["provenance_type"]=="synthetic_demo" for b in bills),
            "electricity_saved":"Not measured","carbon_emissions_avoided":"Not measured",
            "limitations":["Screening signals are not proof of billing error.","No verified official tariff schedule is configured.","Synthetic records are fictional."]}
    finally: con.close()

@app.get("/api/export/summary.html")
def export_summary_html():
    """Download a readable, printable HTML report instead of raw JSON."""
    from fastapi.responses import HTMLResponse
    from html import escape
    con = connect()
    try:
        bills = all_bills(con)
        cases = all_cases(con)
        ds = con.execute("SELECT COUNT(*) readings, COUNT(DISTINCT meter_id) meters, MIN(timestamp) earliest, MAX(timestamp) latest, SUM(CASE WHEN consumption IS NULL THEN 1 ELSE 0 END) missing FROM meter_readings").fetchone()
        imports = con.execute("SELECT source_filename, imported_at, rows_received, rows_accepted, rows_rejected, rows_deduplicated, status FROM dataset_imports ORDER BY imported_at DESC").fetchall()
        rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{:,}</td><td>{:,}</td><td>{:,}</td><td>{:,}</td></tr>".format(
                escape(str(r["source_filename"])), escape(str(r["status"])), int(r["rows_received"]), int(r["rows_accepted"]), int(r["rows_rejected"]), int(r["rows_deduplicated"])
            ) for r in imports
        ) or '<tr><td colspan="6">No imports recorded</td></tr>'
        generated = datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
        html = (
            '<!doctype html><html><head><meta charset="utf-8"><title>ClearWatt Summary Report</title>'
            '<style>body{font:15px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#17233b;max-width:1000px;margin:40px auto;padding:0 24px}'
            '.muted{color:#64748b}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:24px 0}.card{border:1px solid #dbe3ef;border-radius:12px;padding:16px}'
            '.value{font-size:25px;font-weight:700}table{border-collapse:collapse;width:100%;margin:12px 0 28px}th,td{text-align:left;border-bottom:1px solid #e2e8f0;padding:10px;font-size:13px}th{background:#f5f7fb}.note{background:#f5f7fb;padding:14px;border-radius:8px}'
            '@media print{body{margin:10mm;max-width:none}button{display:none}}</style></head><body>'
            '<button onclick="window.print()">Print / Save as PDF</button><h1>ClearWatt Utility Intelligence</h1>'
            f'<div class="muted">Workspace summary · generated {generated}</div><h2>Imported smart-meter dataset</h2><div class="grid">'
            f'<div class="card">Readings stored<div class="value">{int(ds["readings"] or 0):,}</div></div>'
            f'<div class="card">Unique meters<div class="value">{int(ds["meters"] or 0):,}</div></div>'
            f'<div class="card">Missing measurements<div class="value">{int(ds["missing"] or 0):,}</div></div></div>'
            f'<p><b>Date coverage:</b> {escape(str(ds["earliest"] or "—"))} to {escape(str(ds["latest"] or "—"))}</p>'
            '<h2>Import history</h2><table><thead><tr><th>File</th><th>Status</th><th>Received</th><th>Accepted</th><th>Rejected</th><th>Duplicates</th></tr></thead>'
            f'<tbody>{rows}</tbody></table><h2>Bill audit workspace</h2><div class="grid">'
            f'<div class="card">Bill records<div class="value">{len(bills):,}</div></div>'
            f'<div class="card">Investigation cases<div class="value">{len(cases):,}</div></div>'
            f'<div class="card">Cases reviewed<div class="value">{sum(c["status"] != "New" for c in cases):,}</div></div></div>'
            f'<p><b>Verified discrepancies recorded:</b> {sum(c["status"] == "Verified discrepancy" for c in cases):,}</p>'
            '<div class="note"><b>Important limitations</b><ul><li>A screening signal is not proof of an incorrect bill or meter fault.</li>'
            '<li>Smart-meter readings are separate from bill records and are not automatically converted into billed kWh.</li>'
            '<li>No verified official tariff schedule is configured.</li><li>Electricity saved, financial savings and emissions avoided are not measured.</li>'
            '<li>Synthetic demo bills are fictional and must not be represented as real-world outcomes.</li></ul></div></body></html>'
        )
        return HTMLResponse(html, headers={"Content-Disposition": "attachment; filename=clearwatt-summary-report.html"})
    finally:
        con.close()
