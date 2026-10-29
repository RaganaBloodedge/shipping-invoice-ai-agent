"""Deterministic business pipeline around the LLM harness."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from invoice_agent import decide_operation, normalize_record, validate_records
from agent_harness import AgentHarness

class InvoicePipeline:
    def __init__(self, harness: AgentHarness, ledger: Optional[Iterable[Dict[str, Any]]] = None):
        self.harness = harness
        self.ledger = list(ledger or [])

    def process(self, paths: Iterable[str | Path], task: str = "Extract every invoice charge into the required JSON schema") -> list[Dict[str, Any]]:
        self.harness.ingest(paths)
        result = self.harness.run(task)
        # The model may return a JSON array or fenced JSON. Never emit prose as final data.
        raw = result.answer.strip().replace("```json", "").replace("```", "").strip()
        try:
            candidates = json.loads(raw)
        except json.JSONDecodeError:
            candidates = []
        if isinstance(candidates, dict):
            # Accept a convenient model shape and expand one invoice into one
            # output record per charge before applying the strict schema.
            if candidates.get("charges") and not candidates.get("charge_details"):
                base = candidates
                candidates = []
                for charge in base.get("charges", []):
                    candidates.append({
                        "document_info": {"document_type": "Invoice", "reference_no": base.get("invoice_number"), "document_date": base.get("date")},
                        "charge_details": {"charge_item": charge.get("description"), "amount": charge.get("amount"), "currency": base.get("currency")},
                        "audit_trail": {"model_total_amount": base.get("total_amount")},
                    })
            else: candidates = [candidates]
        records = [decide_operation(normalize_record(x), self.ledger) for x in candidates if isinstance(x, dict)]
        validate_records(records)
        return records
