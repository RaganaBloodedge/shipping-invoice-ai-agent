"""The production Agent entry point, implemented with LangChain.

DeepSeek exposes an OpenAI-compatible endpoint, so ChatOpenAI is used as the
LangChain ChatModel. The business rules remain deterministic in invoice_agent.
"""
from __future__ import annotations
import json, os
from typing import Any, Dict, Iterable

from agent_harness import AgentHarness
from invoice_agent import validate_records, normalize_record

def build_langchain_agent(paths: Iterable[str], accounting_entity: str | None = None, ledger: Iterable[Dict[str, Any]] = ()):
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.tools import tool
        from langchain.agents import create_agent
    except ImportError as exc:
        raise RuntimeError("Install langchain, langchain-core and langchain-openai before starting the Agent") from exc

    model = ChatOpenAI(model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), api_key=os.environ["DEEPSEEK_API_KEY"], base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), temperature=0)
    harness = AgentHarness(_LangChainBridge(model))
    harness.ingest(paths)

    @tool
    def search_documents(query: str, top_k: int = 5) -> str:
        """Search loaded EML, PDF, image OCR and office document chunks."""
        return json.dumps(harness._search_documents(query, top_k), ensure_ascii=False)

    @tool
    def get_document(path: str) -> str:
        """Read a loaded document by the exact path returned by search_documents."""
        return json.dumps(harness._get_document(path), ensure_ascii=False)

    tools = [search_documents, get_document]
    system_prompt = "You are a shipping invoice extraction agent. Use tools for evidence. Extract every charge as one record. Return only a JSON array with operation, document_info, transaction_party, charge_details, financial_attributes and audit_trail. Unknown values must be null. Do not invent values."
    return create_agent(model, tools, system_prompt=system_prompt, name="shipping_invoice_agent")

class _LangChainBridge:
    """Small bridge so AgentHarness can still be used for ingestion utilities."""
    def __init__(self, model: Any): self.model = model
    def complete(self, messages, tools): return {"content": self.model.invoke(messages).content}

def run(paths: Iterable[str], task: str = "Extract all invoice charges") -> list[Dict[str, Any]]:
    executor = build_langchain_agent(paths)
    result = executor.invoke({"messages": [{"role": "user", "content": task}]})
    final = result["messages"][-1]
    raw = (getattr(final, "content", "") or str(final)).replace("```json", "").replace("```", "").strip()
    if not raw.startswith("["):
        start, end = raw.find("["), raw.rfind("]")
        if start >= 0 and end > start: raw = raw[start:end + 1]
    records = json.loads(raw)
    if isinstance(records, dict): records = [records]
    # Canonicalise common model aliases into the assignment schema.
    canonical = []
    for item in records:
        op = item.get("operation")
        if isinstance(op, dict) or op not in {"CREATE", "UPDATE", "NO_CHANGE", "PENDING_REVIEW"}: op = "PENDING_REVIEW"
        d, c, f, p = item.get("document_info", {}), item.get("charge_details", {}), item.get("financial_attributes", {}), item.get("transaction_party", {})
        canonical.append(normalize_record({
            "operation": op or "PENDING_REVIEW",
            "document_info": {"document_type": d.get("document_type"), "reference_no": d.get("reference_no", d.get("document_number")), "document_date": d.get("document_date"), "bill_of_lading_no": d.get("bill_of_lading_no", d.get("master_bl_no")), "order_no": d.get("order_no", d.get("our_file_no"))},
            "transaction_party": {"issuer": p.get("issuer", p.get("beneficiary")), "recipient": p.get("recipient", p.get("billed_to", p.get("consignee")))},
            "charge_details": {"charge_item": c.get("charge_item", c.get("description")), "amount": c.get("amount", f.get("amount")), "currency": c.get("currency", f.get("currency")), "payment_term": c.get("payment_term", d.get("terms"))},
            "financial_attributes": {"accounting_entity": f.get("accounting_entity"), "posting_type": f.get("posting_type")},
            "audit_trail": item.get("audit_trail", {})
        }))
    records = canonical
    validate_records(records)
    return records
