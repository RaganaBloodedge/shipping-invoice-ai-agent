# 航运多格式账单解析 AI Agent

这是一个基于 **Python + LangChain + DeepSeek + OCR + Agent Harness** 的航运账单解析智能体。它读取 EML、PDF、PNG/JPG/TIFF、Excel 和 DOCX，提取费用项目，判断 CREATE/UPDATE/NO_CHANGE/PENDING_REVIEW，并输出标准 JSON。

## 安装

要求 Python 3.10+。在项目目录运行：

```bash
python -m pip install -r requirements.txt
```

Windows 也可以使用：

```powershell
py -m pip install -r requirements.txt
```

## 配置 DeepSeek

不要把 Key 写进代码或 Git。PowerShell：

```powershell
$env:DEEPSEEK_API_KEY = "你的DeepSeek API Key"
```

macOS/Linux：

```bash
export DEEPSEEK_API_KEY="你的DeepSeek API Key"
```

## 快速启动

准备一个账单文件 `bill.pdf`，执行：

```bash
python -c "from langchain_agent import run; import json; print(json.dumps(run(['bill.pdf']), ensure_ascii=False, indent=2))"
```

多个文件：

```bash
python -c "from langchain_agent import run; import json; print(json.dumps(run(['mail.eml', 'bill.pdf', 'bill.png']), ensure_ascii=False, indent=2))"
```

## 指定记账主体

AR/AP 必须根据当前记账主体判断。没有记账主体时，系统会返回 `PENDING_REVIEW`：

```python
from langchain_agent import build_langchain_agent
agent = build_langchain_agent(["bill.pdf"], accounting_entity="Origin Agent")
result = agent.invoke({"messages": [{"role": "user", "content": "提取所有费用并判断 AR/AP，只返回标准 JSON 数组"}]})
print(result["messages"][-1].content)
```

## OCR 配置

OCR 客户端在 `invoice_agent.py`：

```python
from invoice_agent import OCRClient
import requests
ocr = OCRClient(endpoint="https://你的OCR服务地址/ai/process", post=requests.post)
```

注入 Harness：

```python
from agent_harness import AgentHarness
from deepseek_llm import DeepSeekLLM
harness = AgentHarness(DeepSeekLLM(), ocr=ocr)
```

OCR 服务需要能够访问文件 URL。本地图片通常需要先上传到对象存储，再把下载 URL 交给 OCR 服务。Pillow 只能读取图片，不能替代 OCR。

## 模块说明

- `langchain_agent.py`：正式 Agent 入口，使用 LangChain `ChatOpenAI`、`@tool` 和 `create_agent`。
- `agent_harness.py`：EML 正文、EML 附件递归解析、PDF、图片、Excel、DOCX 读取、分块和搜索。
- `invoice_agent.py`：费用标准化、金额币种归一化、AR/AP 判断、账单匹配、更新和 JSON 校验。
- `pipeline.py`：把模型返回的账单摘要转换成每个费用项目一条记录。
- `deepseek_llm.py`：DeepSeek 轻量 HTTP 适配器，主要用于连通性测试。

## 输出格式

最终结果是 JSON 数组，每个费用项目一条记录：

```json
[
  {
    "operation": "CREATE",
    "document_info": {
      "document_type": "Debit Note",
      "reference_no": "DC-108478",
      "document_date": "2026-10-24",
      "bill_of_lading_no": null,
      "order_no": null
    },
    "transaction_party": {"issuer": null, "recipient": null},
    "charge_details": {"charge_item": "Handling Charge", "amount": 65.0, "currency": "USD", "payment_term": "COD"},
    "financial_attributes": {"accounting_entity": null, "posting_type": null},
    "audit_trail": {"source_file": "bill.pdf"}
  }
]
```

## 操作类型

- `CREATE`：没有匹配到已有账单；
- `UPDATE`：匹配到账单且有明确字段变化；
- `NO_CHANGE`：匹配到账单但字段没有变化；
- `PENDING_REVIEW`：OCR 不可靠、匹配冲突、缺少记账主体或无法确认。

## 测试

```bash
python -m py_compile agent_harness.py invoice_agent.py pipeline.py langchain_agent.py
python -c "from test_invoice_agent import *; test_normalize_and_create(); test_update_and_validation(); print('tests ok')"
```

示例账单验证见 `SAMPLE_VALIDATION.md`，业务和技术补充思考见 `Thinking.txt`。

## 常见问题

**结果是 `PENDING_REVIEW`？** 通常是缺少记账主体、OCR 不完整、金额校验失败或一个单号匹配到多个账单。

**图片没有文字？** 请配置 OCR 服务；Pillow 本身不做文字识别。

**API Key 放哪里？** 放环境变量或密钥管理服务，不要放入源代码、README 或 Git 历史。
