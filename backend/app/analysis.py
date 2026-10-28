from __future__ import annotations
from datetime import date
from typing import Any
import math
import numpy as np
import pandas as pd

def safe_float(value: Any):
    try:
        if value is None or value == "": return None
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None

def audit_bill(bill: dict) -> dict:
    """Reconcile supplied bill components only; does not validate tariff correctness."""
    fields = ["fixed_charge", "energy_charge", "adjustments", "taxes", "rebates"]
    values = {k: safe_float(bill.get(k)) for k in fields}
    missing = [k for k in ("fixed_charge", "energy_charge") if values[k] is None]
    if missing:
        return {
            "result": "unable_to_verify", "difference": None, "reconstructed_total": None,
            "explanation": "Arithmetic cannot be reconstructed because required components are missing: " + ", ".join(missing) + ".",
            "missing_components": missing, "tariff_status": "Tariff not configured or verified",
        }
    fixed = values["fixed_charge"] or 0
    energy = values["energy_charge"] or 0
    adjustments = values["adjustments"] or 0
    taxes = values["taxes"] or 0
    rebates = values["rebates"] or 0
    reconstructed = fixed + energy + adjustments + taxes - rebates
    reported = safe_float(bill.get("bill_amount"))
    if reported is None:
        return {"result":"unable_to_verify","difference":None,"reconstructed_total":round(reconstructed,2),
                "explanation":"The reported bill amount is missing, so the supplied components cannot be compared with a reported total.",
                "missing_components":["bill_amount"],"tariff_status":"Tariff not configured or verified"}
    diff = round(reported - reconstructed, 2)
    result = "arithmetic_reconciles" if abs(diff) <= 1.0 else "arithmetic_discrepancy"
    explanation = ("The supplied components reconcile with the reported total within ₹1.00 rounding tolerance. This does not verify the readings or tariff." if result == "arithmetic_reconciles"
                   else f"The reported total differs from the supplied component sum by ₹{diff:.2f}. Check omitted components, bill conventions, and source values before drawing a conclusion.")
    return {"result":result,"difference":diff,"reconstructed_total":round(reconstructed,2),"explanation":explanation,
            "missing_components":[],"tariff_status":"Tariff not configured or verified"}

def analyse_bills(bills: list[dict]) -> list[dict]:
    df = pd.DataFrame(bills)
    if df.empty: return []
    for c in ["billed_units_kwh","bill_amount","previous_reading","current_reading"]:
        if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
    if "billing_end" in df:
        df["billing_end"] = pd.to_datetime(df["billing_end"], errors="coerce")
    findings = []
    for idx, row in df.iterrows():
        rowd = row.to_dict()
        signals = []
        prev = row.get("previous_reading")
        curr = row.get("current_reading")
        units = row.get("billed_units_kwh")
        amount = row.get("bill_amount")
        if pd.notna(prev) and pd.notna(curr) and curr < prev:
            signals.append({"rule":"cumulative_reading_drop","severity":"high","points":2,
                "observed":f"Previous reading {prev:g}; current reading {curr:g}",
                "explanation":"The cumulative reading decreased. A reset, meter replacement, rollover, correction, or invalid record may explain it."})
        if pd.notna(units) and units < 0:
            signals.append({"rule":"negative_billed_units","severity":"high","points":2,"observed":f"{units:g} kWh",
                "explanation":"Negative billed units need review unless the source explicitly defines signed/net energy."})
        if pd.notna(amount) and amount < 0:
            signals.append({"rule":"negative_bill_amount","severity":"medium","points":1,"observed":f"₹{amount:g}",
                "explanation":"Negative total amount may represent a credit or adjustment; confirm the source convention."})
        # Historical comparison excludes the current row; only comparable account periods are considered.
        account = str(row.get("account_id",""))
        hist = df[(df.index != idx) & (df["account_id"].astype(str) == account)].copy()
        current_end = row.get("billing_end")
        if pd.notna(current_end) and "billing_end" in hist:
            hist = hist[hist["billing_end"] < current_end]
        hist_units = hist["billed_units_kwh"].dropna().tail(6) if "billed_units_kwh" in hist else pd.Series(dtype=float)
        if pd.notna(units) and len(hist_units) >= 3:
            median = float(hist_units.median())
            mad = float((hist_units - median).abs().median())
            if median > 0 and abs(units-median)/median >= 0.5:
                pct = (units-median)/median*100
                signals.append({"rule":"consumption_deviation","severity":"high" if abs(pct)>=75 else "medium","points":2,
                    "observed":f"{units:g} kWh vs historical median {median:g} kWh ({pct:+.1f}%)",
                    "explanation":"Consumption differs substantially from the available historical median. This is a screening signal, not proof of a billing error."})
            if mad > 0:
                rz = 0.6745 * (units-median) / mad
                if abs(rz) > 3.5:
                    signals.append({"rule":"robust_mad_deviation","severity":"medium","points":1,
                        "observed":f"Robust deviation score {rz:.2f}; history n={len(hist_units)}",
                        "explanation":"The reading is far from the historical median under the configured MAD heuristic. Thresholds require evaluation on appropriate data."})
        if pd.isna(units):
            signals.append({"rule":"missing_billed_units","severity":"low","points":1,"observed":"billed_units_kwh is missing",
                "explanation":"Consumption comparisons are unavailable until billed units are supplied."})
        bill_audit = audit_bill(rowd)
        if bill_audit["result"] == "arithmetic_discrepancy":
            signals.append({"rule":"bill_arithmetic","severity":"high","points":2,
                "observed":f"Reported ₹{amount:.2f}; reconstructed ₹{bill_audit['reconstructed_total']:.2f}; difference ₹{bill_audit['difference']:.2f}",
                "explanation":bill_audit["explanation"]})
        score = min(5, sum(s["points"] for s in signals))
        priority = "High" if score >= 3 or any(s["severity"]=="high" for s in signals) else ("Medium" if score else "Low")
        findings.append({"source_id":str(row.get("bill_id", idx)), "signals":signals, "score":score, "priority":priority,
                         "bill_audit":bill_audit})
    return findings

def quality_report(records: list[dict]) -> dict:
    df = pd.DataFrame(records)
    if df.empty:
        return {"rows":0,"missing_by_field":{},"duplicate_rows":0,"negative_units":0,"invalid_dates":0}
    missing = {str(c):int(df[c].isna().sum() + (df[c].astype(str).str.strip()=="").sum()) for c in df.columns}
    duplicates = int(df.duplicated().sum())
    units = pd.to_numeric(df["billed_units_kwh"], errors="coerce") if "billed_units_kwh" in df else pd.Series(dtype=float)
    dates = pd.to_datetime(df["billing_start"], errors="coerce") if "billing_start" in df else pd.Series(dtype="datetime64[ns]")
    return {"rows":len(df),"missing_by_field":missing,"duplicate_rows":duplicates,
            "negative_units":int((units<0).sum()) if len(units) else 0,
            "invalid_dates":int(dates.isna().sum()) if len(dates) else 0}
