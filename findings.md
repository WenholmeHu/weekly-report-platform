# 邮箱抽取功能分析发现

## 项目上下文

- 项目目标：从 POP3 邮箱按起始日期和主题关键字筛选周报邮件，解析正文、HTML 正文与附件内容，调用兼容 OpenAI Chat Completions 的 LLM 抽取结构化结果，再同步到钉钉多维表。
- 文档确认核心模块：`weekly_report_platform/domain/mail_analysis/email_parser.py` 负责 POP3、正文、附件解析；`service.py` 负责格式化、调用提示词、写分析 JSON；`prompts.py` 负责进度、显式风险、隐藏风险三段 LLM 抽取。
- 运行产物：单封邮件分析结果写到 `runs/<run_id>/mail/risk_json/*.json`。

## 抽取链路

- 文档中的主链路：FastAPI/自动拉取 -> TaskManager -> `fetch_candidate_emails()` -> `EmailService.login_pop3()` -> `find_emails_by_filter()` -> `get_email_by_id()` -> `analyze_email()` -> `format_email_for_analysis()` -> `process_email_analysis_with_errors()` -> LLM -> JSON artifact。
- `runtime.py` 逐封处理候选邮件：未命中本地已处理状态时，进入 `analyze_email()`；分析完成后读取 payload 并交给钉钉同步。
- `service.py` 的单封分析链路：`format_email_for_analysis(email_data)` 生成统一文本 -> `process_email_analysis_with_errors(formatted_content, subject)` 执行三段 LLM 抽取 -> `build_output_payload()` 合并邮件元信息、source 元信息、analysis 和 errors -> `write_analysis_artifact()` 写 JSON。

## 正文抽取

- MIME 解析阶段将非附件的 `text/plain` 放入 `text_body`，非附件的 `text/html` 放入 `html_body`。
- 正文格式化阶段优先使用 HTML 正文：`html_body` 存在时用 MarkItDown 转 Markdown；失败时用正则剥 HTML 标签；没有 HTML 才使用 `text_body`。
- 如果正文为空，会写入占位文本 `[Empty email body; this email may contain attachments only]`，让模型知道正文缺失但仍可看附件。

## 附件抽取

- MIME 解析阶段把 `application/octet-stream` 或带 `attachment` disposition 的 part 作为附件，保存 `filename`、`content_type`、二进制 `data`、`size`。
- 格式化阶段按文件扩展名解析附件：Excel、Word、PDF、TXT 有专门解析器；不支持的类型只写 `[Unsupported attachment type: .ext]`，不让整封邮件失败。
- Excel：用 openpyxl 读取所有 sheet，转 Markdown 表格，并保留 sheet 名。
- Word：用 python-docx 提取非空段落文本。
- PDF：用 PyMuPDF 按页提取文本，每页前加页码标题。
- TXT：依次尝试 utf-8、gb18030、gbk、gb2312、latin-1 解码。

## 支持格式

- 正文：`text/plain`、`text/html`。
- 附件内容解析：`.xlsx`、`.xls`、`.docx`、`.doc`、`.pdf`、`.txt`。
- 邮件头/正文中文编码：实现中针对 header 与 payload 有 utf-8、gb18030、gbk、gb2312 等兜底。

## 输出内容

- artifact 顶层保留：`message_id`、`subject`、`from`、`to`、`cc`、`date`、`processed_at`、`source`、`analysis`、`errors`。
- `source` 保留：是否有文本正文、是否有 HTML 正文、附件名列表、附件数量。
- `analysis` 的核心业务抽取：`进度抽取结果`、`风险抽取结果`、`隐藏风险`。
- 默认钉钉字段消费：邮件标识、项目周报、进度分析、风险详情、隐藏风险。
