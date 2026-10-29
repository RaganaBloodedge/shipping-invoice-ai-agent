"""Shipping invoice domain model, normalisation, matching and validation."""
from __future__ import annotations
import json, re
from copy import deepcopy
from typing import Any, Dict, Iterable, Optional

OPERATIONS = {"CREATE", "UPDATE", "NO_CHANGE", "PENDING_REVIEW"}
CURRENCIES = {"USD", "CNY", "EUR", "GBP", "JPY", "HKD", "SGD"}
CURRENCY_ALIASES = {"RMB": "CNY", "￥": "CNY", "¥": "CNY", "$": "USD", "US$": "USD"}
CHARGE_ALIASES = {
    "handling fee": "Handling Charge", "h/d fee": "Handling Charge", "handling charge": "Handling Charge",
    "doc fee": "Documentation Fee", "documentation fee": "Documentation Fee",
    "thc": "Terminal Handling Charge", "terminal handling charge": "Terminal Handling Charge",
    "trucking fee": "Trucking Charge", "trucking charge": "Trucking Charge",
    "customs fee": "Customs Clearance Fee", "customs clearance fee": "Customs Clearance Fee",
    "storage fee": "Storage Charge", "storage charge": "Storage Charge",
}
NULL_FIELDS = {
    "document_info": ("document_type", "reference_no", "document_date", "bill_of_lading_no", "order_no"),
    "transaction_party": ("issuer", "recipient"),
    "charge_details": ("charge_item", "amount", "currency", "payment_term"),
    "financial_attributes": ("accounting_entity", "posting_type"),
}

def _norm(v: Any) -> str: return re.sub(r"\s+", " ", str(v or "").strip())
def currency(v: Any) -> Optional[str]:
    s = _norm(v).upper()
    return CURRENCY_ALIASES.get(s, s) or None
def amount(v: Any) -> Optional[float]:
    if v is None or v == "": return None
    m = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None
def normalize_charge_name(v: Any) -> Optional[str]:
    s = _norm(v); return CHARGE_ALIASES.get(s.lower(), s) or None

def normalize_record(raw: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc, party = raw.get("document_info", {}), raw.get("transaction_party", {})
    charge, fin = raw.get("charge_details", {}), raw.get("financial_attributes", {})
    out: Dict[str, Any] = {"operation": raw.get("operation", "PENDING_REVIEW")}
    for section, keys, src in (("document_info", NULL_FIELDS["document_info"], doc), ("transaction_party", NULL_FIELDS["transaction_party"], party), ("charge_details", NULL_FIELDS["charge_details"], charge), ("financial_attributes", NULL_FIELDS["financial_attributes"], fin)):
        out[section] = {k: src.get(k) for k in keys}
    out["charge_details"]["charge_item"] = normalize_charge_name(out["charge_details"]["charge_item"])
    out["charge_details"]["amount"] = amount(out["charge_details"]["amount"])
    out["charge_details"]["currency"] = currency(out["charge_details"]["currency"])
    if out["financial_attributes"]["posting_type"] not in {"AR", "AP", None}: out["financial_attributes"]["posting_type"] = None
    audit = deepcopy(raw.get("audit_trail") or {})
    if source: audit.update({"source_file": source.get("path"), "source_text": source.get("text", "")[:4000]})
    out["audit_trail"] = audit
    if out["charge_details"]["currency"] not in CURRENCIES and out["charge_details"]["currency"] is not None: out["operation"] = "PENDING_REVIEW"
    return out

def infer_posting_type(record: Dict[str, Any], accounting_entity: Optional[str]) -> Dict[str, Any]:
    p, entity = record["transaction_party"], _norm(accounting_entity)
    if entity and entity == _norm(p.get("issuer")): record["financial_attributes"]["posting_type"] = "AR"
    elif entity and entity == _norm(p.get("recipient")): record["financial_attributes"]["posting_type"] = "AP"
    else: record["operation"] = "PENDING_REVIEW"
    record["financial_attributes"]["accounting_entity"] = accounting_entity
    return record

def _same(a: Any, b: Any) -> bool: return _norm(a).lower() == _norm(b).lower() if a is not None and b is not None else a == b
def find_matches(new: Dict[str, Any], existing: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    di, ch = new["document_info"], new["charge_details"]
    result = []
    for old in existing:
        odi, och = old.get("document_info", {}), old.get("charge_details", {})
        keys = [(di.get("reference_no"), odi.get("reference_no")), (di.get("bill_of_lading_no"), odi.get("bill_of_lading_no")), (di.get("order_no"), odi.get("order_no"))]
        stable = any(a and b and _same(a, b) for a, b in keys)
        stable = stable and _same(ch.get("charge_item"), och.get("charge_item")) and (not ch.get("currency") or _same(ch.get("currency"), och.get("currency")))
        if stable: result.append(old)
    return result

def decide_operation(record: Dict[str, Any], existing: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    r = normalize_record(record)
    matches = find_matches(r, existing)
    if len(matches) > 1:
        r["operation"] = "PENDING_REVIEW"; r["audit_trail"]["conflict"] = "multiple ledger matches"; return r
    if not matches: r["operation"] = "CREATE"; return r
    old = matches[0]; changed = []
    for section, keys in NULL_FIELDS.items():
        for key in keys:
            if r[section][key] is not None and not _same(r[section][key], old.get(section, {}).get(key)): changed.append(f"{section}.{key}")
    if not changed: r["operation"] = "NO_CHANGE"
    else:
        r["operation"] = "UPDATE"; r["audit_trail"]["changed_fields"] = changed
        for section, keys in NULL_FIELDS.items():
            for key in keys:
                if r[section][key] is None: r[section][key] = old.get(section, {}).get(key)
    return r

def validate_records(records: Iterable[Dict[str, Any]]) -> None:
    for r in records:
        if r.get("operation") not in OPERATIONS: raise ValueError("invalid operation")
        for section, keys in NULL_FIELDS.items():
            if set(r.get(section, {})) != set(keys): raise ValueError(f"schema mismatch in {section}")
        c, a = r["charge_details"]["currency"], r["charge_details"]["amount"]
        if c is not None and c not in CURRENCIES: raise ValueError("currency must be ISO 4217")
        if a is not None and not isinstance(a, (int, float)): raise ValueError("amount must be numeric")
        json.dumps(r, ensure_ascii=False, allow_nan=False)

def detect_duplicates(records: Iterable[Dict[str, Any]]) -> list[str]:
    seen, dup = set(), []
    for r in records:
        k = (r["document_info"].get("reference_no"), r["charge_details"].get("charge_item"))
        if k in seen: dup.append(f"{k[0]}:{k[1]}")
        seen.add(k)
    return dup
