# 邮箱抽取功能报告

生成日期：2026-05-13

## 1. 结论概览

本项目的“邮箱抽取”不是抽取邮箱地址，而是从 POP3 邮箱中筛选周报邮件，解析邮件正文与附件，并调用 LLM 抽取结构化周报内容。代码中本地解析负责把邮件整理成统一文本，业务语义抽取由 `prompts.py` 中的三段提示词完成。

核心能力如下：

- 从 POP3 邮箱按 `start_date` 和 `subject_keyword` 筛选候选邮件。
- 解析邮件头、正文、HTML 正文和附件。
- 将正文和附件统一格式化成一段分析文本。
- 调用 LLM 抽取进度、显式风险和隐藏风险。
- 将结果写入 `runs/<run_id>/mail/risk_json/*.json`，再按钉钉字段配置同步。

## 2. 抽取逻辑链

整体链路如下：

```text
页面/自动任务配置
  -> TaskManager.start_task()
  -> fetch_candidate_emails(start_date, subject_keyword)
  -> EmailService.login_pop3()
  -> EmailService.find_emails_by_filter()
  -> EmailService.get_email_header_only()
  -> EmailService.get_email_by_id()
  -> EmailService._parse_email()
  -> analyze_email()
  -> format_email_for_analysis()
  -> process_email_analysis_with_errors()
  -> LLM 三段抽取
  -> build_output_payload()
  -> write_analysis_artifact()
  -> sync_analysis_record()
```

分阶段说明：

1. 任务入口读取已保存配置或前端配置，拿到 `start_date`、`subject_keyword`、`force_refresh`。
2. `fetch_candidate_emails()` 将 `start_date` 解析为日期，登录 POP3 邮箱。
3. `find_emails_by_filter()` 从新到旧扫描邮件，先用 `top(message_id, 0)` 只拉邮件头。
4. 邮件头命中条件后才用 `retr(message_id)` 拉完整邮件，减少网络和解析成本。
5. `_parse_email()` 解析完整 MIME 邮件，得到邮件元信息、正文、HTML 正文、附件二进制。
6. `format_email_for_analysis()` 将元信息、正文、附件内容拼成统一文本。
7. `process_email_analysis_with_errors()` 调用 LLM：
   - 进度抽取与显式风险抽取并发执行。
   - 隐藏风险抽取依赖显式风险的原文抽取结果，用于避免重复。
8. `build_output_payload()` 合并邮件元信息、source 摘要、分析结果和错误信息。
9. 分析结果落盘，默认同步层再取其中的 `message_id`、`subject`、进度分析、风险详情、隐藏风险写入钉钉。

## 3. 候选邮件怎么筛选

筛选发生在 POP3 拉取阶段，条件是日期和主题关键字：

- 邮件连接：使用 `poplib.POP3_SSL`。
- 日期来源：邮件头 `Date`。
- 主题来源：邮件头 `Subject`，会解码 MIME header。
- 主题匹配：`subject_keyword in subject`，默认关键字是 `周报`。
- 扫描顺序：从邮箱最新邮件向旧邮件扫描。
- 提前停止：连续遇到 10 封早于起始日期的邮件后停止扫描。
- 完整拉取：只有日期和主题检查后命中的邮件，才拉完整邮件正文与附件。

这一阶段只决定“哪些邮件要分析”，不做进度、风险等业务字段抽取。

## 4. 抽取的内容是什么

最终 artifact 顶层结构包括：

```json
{
  "message_id": "邮件唯一标识",
  "subject": "邮件主题",
  "from": "发件人",
  "to": "收件人",
  "cc": "抄送",
  "date": "邮件日期",
  "processed_at": "处理时间",
  "source": {
    "has_text_body": true,
    "has_html_body": false,
    "attachment_names": ["周报.xlsx"],
    "attachment_count": 1
  },
  "analysis": {},
  "errors": ""
}
```

其中 `analysis` 是核心业务抽取结果，包含三类：

- `进度抽取结果`
  - `原文抽取`：模型认为与进度相关的正文或附件原文片段。
  - `分析结果`
    - `综合总结`：进度总结。
    - `一致性分析`：多来源内容是否一致；只有一个来源时提示词要求返回不适用。
- `风险抽取结果`
  - `原文抽取`：显式风险原文片段。
  - `一致性分析`：风险内容在不同来源之间的一致性判断。
  - `风险详情`：风险数组，每条包含序号、风险描述、风险类型、风险措施和最新进展、风险责任人。
- `隐藏风险`
  - 数组结构，每条包含序号、来源、风险描述、风险类型、风险归纳、风险措施和最新进展。
  - 该阶段会把显式风险的原文片段作为已知风险传入提示词，要求模型避免重复。

默认钉钉配置实际同步这些字段：

| 业务字段 | 钉钉字段名 | 来源路径 |
|---|---|---|
| 邮件标识 | 邮件标识 | `message_id` |
| 项目周报 | 项目周报 | `subject` |
| 进度分析 | 进度分析 | `analysis.进度抽取结果.分析结果` |
| 风险详情 | 风险详情 | `analysis.风险抽取结果.风险详情` |
| 隐藏风险 | 隐藏风险 | `analysis.隐藏风险` |

## 5. 支持什么格式

正文支持：

| 类型 | 处理方式 |
|---|---|
| `text/plain` | 作为纯文本正文解析 |
| `text/html` | 优先作为 HTML 正文解析，并转 Markdown |

附件支持：

| 扩展名 | 解析方式 |
|---|---|
| `.xlsx` / `.xls` | 使用 `openpyxl` 读取所有 sheet，转 Markdown 表格 |
| `.docx` / `.doc` | 走 Word 附件解析逻辑，使用 `python-docx` 提取段落文本 |
| `.pdf` | 使用 PyMuPDF 按页提取文本 |
| `.txt` | 按多种编码尝试解码为文本 |

编码支持与兜底：

- 邮件头：优先使用声明编码，再尝试 `utf-8`、`gb18030`、`gbk`、`gb2312`。
- 正文：优先使用 MIME charset，再尝试 `gb2312`、`gbk`、`gb18030`、`utf-8`。
- TXT 附件：尝试 `utf-8`、`gb18030`、`gbk`、`gb2312`、`latin-1`。

不支持的附件类型不会导致整封邮件失败，而是在模型输入里写入：

```text
[Unsupported attachment type: .ext]
```

注意：代码分支把 `.doc` 和 `.docx` 都交给 `python-docx`，但 `python-docx` 对旧版二进制 `.doc` 的实际兼容性通常弱于 `.docx`，生产使用时建议重点验证 `.doc` 样本。

## 6. 正文中是怎么抽的

正文处理分两层：先本地解析，再交给 LLM 抽取。

本地解析：

1. 遍历 MIME part。
2. 如果 `content_type == "text/plain"` 且不是附件，解码后追加到 `text_body`。
3. 如果 `content_type == "text/html"` 且不是附件，解码后追加到 `html_body`。
4. 解析完成后进入 `format_email_for_analysis()`。

正文格式化：

1. 先写入邮件元信息块：Subject、From、Date。
2. 再写入 `[Email Body]` 块。
3. 如果存在 `html_body`，优先使用 HTML：
   - 替换 HTML 内的 `gb2312`、`gbk` charset 声明为 `utf-8`。
   - 写入临时 HTML 文件。
   - 使用 MarkItDown 转 Markdown。
   - 清理 `&nbsp;`、HTML 实体和过多空行。
   - 如果 MarkItDown 转换失败，则用正则剥掉 HTML 标签作为兜底。
4. 如果没有 HTML 正文，但存在 `text_body`，直接使用纯文本正文。
5. 如果两者都没有，写入空正文占位，提示模型该邮件可能只有附件。

业务抽取：

- 代码不使用固定规则从正文里直接抓取“进度”或“风险”。
- 正文会作为统一文本的一部分传给 LLM。
- 进度提示词要求模型关注 summary/progress sections，并输出 `原文抽取` 与 `分析结果`。
- 显式风险提示词要求模型从正文和附件中抽取 explicit weekly report risks，并输出 `风险详情`。
- 隐藏风险提示词要求模型识别显式风险之外的隐含风险。

因此，正文中的具体业务字段抽取是“模型语义抽取”，不是正则或关键词硬编码抽取。

## 7. 附件中是怎么抽取的

附件同样分为本地文本化和 LLM 语义抽取两层。

MIME 附件识别：

1. 遍历 MIME part。
2. 如果 `content_type == "application/octet-stream"`，或 `Content-Disposition` 中包含 `attachment`，则视为附件。
3. 读取并解码附件文件名。
4. 用 `part.get_payload(decode=True)` 取二进制内容。
5. 附件对象保存 `filename`、`content_type`、`data`、`size`。

附件格式化：

每个附件都会在统一文本里形成一个独立块：

```text
============================================================
[Attachment: 文件名]
============================================================
附件解析后的文本
```

不同格式的处理方式：

- Excel：
  - 用 `openpyxl.load_workbook(..., data_only=True)` 读取计算后的单元格值。
  - 遍历所有 worksheet。
  - 将每个 sheet 的非空行转成 Markdown 表格。
  - 每个 sheet 前加入 `### Sheet: sheet名`。
  - 百分比格式会转成百分比字符串，单元格中的 `|` 会转义，换行会转为 `<br>`。
- Word：
  - 用 `python-docx` 读取文档。
  - 提取所有非空段落，按换行拼接。
- PDF：
  - 用 PyMuPDF 从二进制流打开 PDF。
  - 按页调用 `get_text()`。
  - 每页前加入 `=== Page N ===`。
- TXT：
  - 多编码尝试解码，成功后返回完整文本。

业务抽取：

- 附件文本会和正文一起传给同一组 LLM 提示词。
- 提示词明确说明输入来自 email content and attachments。
- 模型输出中的 `来源` 字段用于标识内容来自 body 或 attachment。
- 代码没有单独为附件配置一套业务规则，附件与正文在模型侧一起参与进度、显式风险和隐藏风险判断。

## 8. 错误与边界行为

- 单个附件解析失败时，当前附件块写入失败提示，例如 `[Failed to parse Excel attachment]`，整封邮件仍继续分析。
- 不支持附件类型只写标记，不终止分析。
- LLM 返回必须是严格 JSON：
  - 进度抽取必须是 JSON object。
  - 显式风险抽取必须是 JSON object。
  - 隐藏风险抽取必须是 JSON array。
- 模型返回外层 Markdown 代码块时，会先清理 ```json / ``` 包裹再解析。
- 任一抽取阶段失败会把错误加入 `errors`，可成功的其他阶段仍会保留结果。
- 同步层看到分析结果 `errors` 非空时会跳过写入钉钉，避免把不完整分析结果写入业务表。

## 9. 关键源码位置

| 文件 | 作用 |
|---|---|
| `weekly_report_platform/domain/mail_analysis/email_parser.py` | POP3 登录、候选邮件筛选、MIME 解析、正文和附件格式化 |
| `weekly_report_platform/domain/mail_analysis/service.py` | 单封邮件分析编排、artifact 构造与落盘 |
| `weekly_report_platform/domain/mail_analysis/prompts.py` | 三段 LLM 抽取提示词、JSON 解析和错误收集 |
| `weekly_report_platform/runtime.py` | 后台任务中逐封邮件执行“分析 -> 同步” |
| `weekly_report_platform/resources/config.local.json` | 默认钉钉字段与 `sourcePath` 映射 |
| `weekly_report_platform/dingtalk.py` | 将抽取结果按字段类型转换为钉钉单元格文本 |

## 10. 可验证的测试覆盖

现有测试覆盖了以下和抽取相关的能力：

- UTF-8 邮件主题解码。
- UTF-8 正文解析。
- 中文附件名解码。
- GB18030 TXT 附件解析。
- Excel 多 sheet 解析并包含所有 sheet 内容。
- `analyze_email()` 能写出包含 `analysis` 的 JSON artifact。
- LLM 进度抽取拒绝非 JSON object。
- 隐藏风险抽取能处理异常形态的显式风险原文抽取结果。

建议后续补充的测试：

- HTML 正文转 Markdown 和 MarkItDown 失败兜底。
- PDF 附件按页提取文本。
- Word `.docx` 附件段落提取。
- 不支持附件类型的占位行为。
- `.doc` 旧格式附件的真实样本兼容性。
