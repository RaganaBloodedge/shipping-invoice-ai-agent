"""Optional LangChain adapter. The core harness stays provider-agnostic."""
from typing import Any, Dict, List

class LangChainLLM:
    def __init__(self, model: Any):
        self.model = model

    def complete(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Works with LangChain chat models while keeping the harness contract tiny.
        result = self.model.invoke(messages)
        content = getattr(result, "content", result)
        tool_calls = getattr(result, "tool_calls", None) or []
        return {"content": content, "tool_calls": tool_calls}
