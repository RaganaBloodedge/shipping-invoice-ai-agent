# 示例账单验证记录

已读取目录 `【批量下载】PDF 示例账单2等/笔试题AIAGENT`，发现：

- 1 个 EML 邮件；
- 2 个 PDF 附件/示例账单；
- 1 张 PNG 账单图片。

EML 解析结果已验证：邮件主题、From、To、正文均可提取，并发现两个附件：

- `4055331281 MBL.pdf`
- `4055331281 HBL.pdf`

Harness 会把这两个附件递归加入文档集合，而不是只保留附件文件名。

图片示例的可见字段包括：

- 单据类型：Debit Invoice；
- 参考号：`ORFR1618731`；
- 单据日期：`10/03/2025`；
- 船名：`MSC ELODIE`；
- Booking Number：`ZIMUNGB1312144`；
- B/L：`ZIMUNGB1312144`；
- 付款方式：`PAY IMMEDIATELY`；
- 费用：`WHARFAGE ADMINISTRATION FEE`，USD 5.00；
- 费用：`WHARFAGE`，USD 21.90；
- 合计：USD 26.90。

图片和扫描 PDF 的文字识别依赖 `AgentHarness(ocr=...)` 注入的 OCR 客户端；没有 OCR 服务时，Harness 会保留来源并返回待审核状态，不会把空结果当成已入账数据。
