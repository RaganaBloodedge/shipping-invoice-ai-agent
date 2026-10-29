"""Minimal DeepSeek OpenAI-compatible adapter for the harness.

The API key is read from an environment variable and is never persisted.
"""
from __future__ import annotations
import json, os, urllib.request
from urllib.error import HTTPError
from typing import Any, Dict, List

class DeepSeekLLM:
    def __init__(self, api_key: str | None = None, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com/chat/completions"):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key: raise ValueError("DEEPSEEK_API_KEY is required")
        self.model, self.base_url = model, base_url

    def complete(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        # Keep the harness tool contract provider-neutral. DeepSeek can receive
        # these as JSON instructions even when native tool schemas are disabled.
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": {"type": "object", "properties": {k: {"type": ("integer" if v == "integer" else "string")} for k, v in t.get("parameters", {}).items()}}}} for t in tools]
        req = urllib.request.Request(self.base_url, data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as response: body = json.loads(response.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"DeepSeek API {exc.code}: {detail[:1000]}") from exc
        msg = body.get("choices", [{}])[0].get("message", {})
        calls = []
        for call in msg.get("tool_calls", []) or []:
            fn = call.get("function", call)
            calls.append({"id": call.get("id", "call_" + str(len(calls))), "name": fn.get("name"), "arguments": fn.get("arguments", {})})
        return {"content": msg.get("content", ""), "tool_calls": calls}
