"""LLM-agnostic harness for a multi-format document parsing agent.

The harness owns orchestration, tool execution, limits, tracing and result
normalisation.  A model is supplied through the small ``LLM`` protocol.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import tempfile
from email import policy
from email.parser import BytesParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Protocol


class LLM(Protocol):
    def complete(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> Dict[str, Any]: ...


@dataclass
class AgentConfig:
    max_steps: int = 8
    max_chars: int = 120_000
    max_tool_output_chars: int = 20_000


@dataclass
class AgentResult:
    answer: str
    documents: List[Dict[str, Any]] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)


def _read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            pass
    return path.read_text(errors="replace")


def parse_document(path: str | Path, ocr: Any = None) -> Dict[str, Any]:
    """Parse common formats, degrading gracefully when optional libraries are absent."""
    p = Path(path)
    ext = p.suffix.lower()
    text = ""
    meta: Dict[str, Any] = {"path": str(p), "name": p.name, "extension": ext}
    if ext == ".eml":
        msg = BytesParser(policy=policy.default).parsebytes(p.read_bytes())
        parts = [f"Subject: {msg.get('subject', '')}", f"From: {msg.get('from', '')}", f"To: {msg.get('to', '')}"]
        if msg.is_multipart():
            parts += [x.get_content() for x in msg.walk() if x.get_content_type() == "text/plain"]
        else: parts.append(msg.get_content())
        text = "\n".join(str(x) for x in parts)
        meta["attachments"] = []
        for attachment in msg.iter_attachments():
            name = attachment.get_filename() or "attachment"
            meta["attachments"].append(name)
            # Preserve attachment bytes for the caller; the harness can recursively ingest them.
            meta.setdefault("attachment_bytes", []).append({"name": name, "content": attachment.get_payload(decode=True) or b""})
    elif ext in {".txt", ".md", ".rst", ".json", ".csv", ".log", ".xml", ".html", ".htm"}:
        text = _read_text(p)
        if ext in {".html", ".htm"}:
            text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text, flags=re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
    elif ext == ".pdf":
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(p))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            meta["pages"] = len(reader.pages)
        except ImportError:
            text = "[PDF parser unavailable: install pypdf]"
        if not text.strip() and ocr:
            text = str(ocr(str(p)))
    elif ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        text = str(ocr(str(p))) if ocr else "[OCR client required for image input]"
    elif ext in {".docx"}:
        try:
            from docx import Document  # type: ignore
            doc = Document(str(p))
            text = "\n".join(x.text for x in doc.paragraphs)
            meta["tables"] = len(doc.tables)
        except ImportError:
            text = "[DOCX parser unavailable: install python-docx]"
    elif ext in {".xlsx", ".xls"}:
        try:
            import openpyxl  # type: ignore
            book = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
            text = "\n".join(" | ".join("" if v is None else str(v) for v in row)
                               for sheet in book.worksheets for row in sheet.iter_rows(values_only=True))
            meta["sheets"] = book.sheetnames
        except ImportError:
            text = "[Excel parser unavailable: install openpyxl]"
    else:
        text = _read_text(p)
    meta["mime"] = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    meta["chars"] = len(text)
    return {"text": text, "metadata": meta}


def chunk_text(text: str, size: int = 4000, overlap: int = 300) -> List[str]:
    if size <= overlap:
        raise ValueError("chunk size must be larger than overlap")
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


class AgentHarness:
    def __init__(self, llm: LLM, config: AgentConfig | None = None, ocr: Any = None):
        self.llm, self.config, self.ocr = llm, config or AgentConfig(), ocr
        self.tools: Dict[str, Callable[..., Any]] = {
            "search_documents": self._search_documents,
            "get_document": self._get_document,
        }
        self.docs: Dict[str, Dict[str, Any]] = {}

    def ingest(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            parsed = parse_document(path, self.ocr)
            parsed["chunks"] = chunk_text(parsed["text"])
            self.docs[str(Path(path))] = parsed
            # EML attachments are first-class inputs and are recursively parsed.
            for attachment in parsed["metadata"].get("attachment_bytes", []):
                suffix = Path(attachment["name"]).suffix or ".bin"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                    f.write(attachment["content"])
                    temp_name = f.name
                try:
                    child = parse_document(temp_name, self.ocr)
                    child["metadata"]["parent_document"] = str(path)
                    child["metadata"]["attachment_name"] = attachment["name"]
                    child["chunks"] = chunk_text(child["text"])
                    self.docs[f"{path}::{attachment['name']}"] = child
                finally:
                    try: os.unlink(temp_name)
                    except OSError: pass

    def _get_document(self, path: str) -> Dict[str, Any]:
        doc = self.docs.get(str(Path(path)))
        if not doc:
            return {"error": "document not found", "path": path}
        return {"metadata": doc["metadata"], "text": doc["text"][: self.config.max_tool_output_chars]}

    def _search_documents(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        try: top_k = int(top_k)
        except (TypeError, ValueError): top_k = 5
        terms = [x.lower() for x in re.findall(r"\w+", query) if len(x) > 1]
        hits = []
        for path, doc in self.docs.items():
            for i, chunk in enumerate(doc["chunks"]):
                score = sum(chunk.lower().count(term) for term in terms)
                if score:
                    hits.append({"path": path, "chunk": i, "score": score, "text": chunk})
        return sorted(hits, key=lambda x: x["score"], reverse=True)[:top_k]

    def run(self, task: str) -> AgentResult:
        system = ("You are a document analysis agent. Use tools when evidence is needed. "
                  "Return a concise answer grounded in retrieved document text.")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
        tool_specs = [
            {"name": "search_documents", "description": "Find relevant document chunks", "parameters": {"query": "string", "top_k": "integer"}},
            {"name": "get_document", "description": "Read a document", "parameters": {"path": "string"}},
        ]
        trace: List[Dict[str, Any]] = []
        for step in range(self.config.max_steps):
            response = self.llm.complete(messages, tool_specs)
            trace.append({"step": step, "response": response})
            calls = response.get("tool_calls") or []
            if calls:
                assistant_calls = []
                tool_messages = []
                for call in calls:
                    name, args = call.get("name"), call.get("arguments", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except json.JSONDecodeError: args = {}
                    if name not in self.tools:
                        result = {"error": f"unknown tool: {name}"}
                    else:
                        try:
                            result = self.tools[name](**args)
                        except Exception as exc:  # tool failures are model-visible
                            result = {"error": str(exc)}
                    payload = json.dumps(result, ensure_ascii=False)[: self.config.max_tool_output_chars]
                    call_id = call.get("id", f"call_{step}")
                    assistant_calls.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})
                    tool_messages.append({"role": "tool", "tool_call_id": call_id, "content": payload})
                messages.append({"role": "assistant", "content": response.get("content") or "", "tool_calls": assistant_calls})
                messages.extend(tool_messages)
                continue
            answer = str(response.get("content", response.get("answer", "")))
            return AgentResult(answer=answer, documents=list(self.docs), trace=trace)
        return AgentResult("Agent stopped after reaching max_steps.", list(self.docs), trace)


class EchoLLM:
    """Deterministic smoke-test model; replace with an API adapter in production."""
    def complete(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"content": "LLM adapter connected. Provide a model implementation for reasoning."}


if __name__ == "__main__":
    paths = sys.argv[1:-1]
    task = sys.argv[-1] if len(sys.argv) > 1 else "Summarize the documents."
    harness = AgentHarness(EchoLLM())
    harness.ingest(paths)
    print(harness.run(task).answer)
