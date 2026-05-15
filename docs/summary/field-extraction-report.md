# 钉钉多维表字段抽取链路报告

## 总览：数据流

```
[邮箱 POP3]
  │  subject_keyword="周报", start_date, max_emails
  ▼
[email_parser.py]  find_emails_by_filter()  ──  候选邮件列表
  │
  ▼  format_email_for_analysis()
  │
[统一Markdown文本]
  ├─ [Email Metadata]  Subject / From / Date
  ├─ [Email Body]       HTML优先(MarkItDown转MD) / 纯文本回退
  └─ [Attachment: xxx]  Excel→MD表格 / Word→段落 / PDF→逐页
  │
  ▼  asyncio.gather(并发)
[prompts.py]
  ├─ extract_progress_from_report()  →  LLM 进度抽取
  └─ extract_risk_from_report()      →  LLM 风险抽取
  │
  ▼
[analysis dict]
  {
    "进度抽取结果": { 一致性分析, 项目整体进展, 项目名称, 项目编号, 项目周报周期, 项目进度, 里程碑, 本周小结, 下周计划, 原文抽取[] },
    "风险抽取结果": { 原文抽取[], 一致性分析, 风险详情[{序号, 风险类型, 风险等级, 风险描述, 风险措施和最新进展, 风险责任人, 计划解决日期}] }
  }
  │
  ▼  build_output_payload() + write_analysis_artifact()
[JSON artifact]  runs/<run_id>/mail/risk_json/<message_id>.json
  {
    "message_id": "...", "subject": "...", "from": "...", "to": "...", "cc": "...", "date": "...",
    "processed_at": "2026-05-15T01:21:42.621229+00:00",
    "source": { has_text_body, has_html_body, attachment_names[], attachment_count },
    "analysis": { 进度抽取结果: {...}, 风险抽取结果: {...} },
    "errors": ""
  }
  │
  ▼  dingtalk.py  resolve_source_value() → normalize_cell_value()
[钉钉多维表]  baseId: ZX6GRezwJl7DbxOxirwj3wGzVdqbropQ  tableId: XD9FUh1
```

---

## 字段配置

配置来源：`weekly_report_platform/resources/config.local.json`（11个字段）

---

## 各字段抽取链路

### 1. 邮件标识 (`message_id`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 邮件标识 |
| 类型 | text |
| 是否必填 | 否 |
| sourcePath | `message_id`（artifact顶层） |

**数据来源**：MIME邮件 `Message-ID` 请求头。`email_parser.py` 第117行提取后清理空白字符，存入artifact顶层。

**抽取逻辑**：不经过LLM，直接从邮件头解析。

**钉钉映射**：`artifact.message_id` → `stringify_text_part()` → 原样文本写入。

**特殊用途**：同步去重的核心键。`dingtalk.py` 在写入前通过 `message_id` 调用 `query_records` 检查表中是否已存在相同记录，避免重复写入（第570行）。同时也在本地manifest中记录已同步的 `message_id`，做双重去重。

---

### 2. 项目名称 (`project_name`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 项目名称 |
| 类型 | text |
| 是否必填 | 是 |
| sourcePath | `analysis.进度抽取结果.项目名称` |

**数据来源**：邮件正文或附件（Excel/Word/PDF）中的项目名称文本。

**抽取逻辑**：
1. `email_parser.py` 的 `format_email_for_analysis()` 将邮件组装成统一Markdown文本
2. LLM并发收到进度抽取提示词（`prompts.py` 第96-130行），提示词要求返回JSON中的 `"项目名称"` 字段
3. 提示词规则：从邮件正文/附件中"按适当方式提取"

**钉钉映射**：`artifact.analysis.进度抽取结果.项目名称`（字符串）→ `stringify_text_part()` → 原样文本写入。

---

### 3. 项目编号 (`project_code`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 项目编号 |
| 类型 | text |
| 是否必填 | 是 |
| sourcePath | `analysis.进度抽取结果.项目编号` |

**数据来源**：同项目名称，从邮件内容中提取。

**抽取逻辑**：进度抽取提示词（`prompts.py` 第122行）要求LLM返回 `"项目编号"` 字段。

**钉钉映射**：同项目名称，字符串直接写入。

---

### 4. 项目周报周期 (`project_cycle`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 项目周报周期 |
| 类型 | text |
| 是否必填 | 是 |
| sourcePath | `analysis.进度抽取结果.项目周报周期` |

**数据来源**：邮件主题、正文或附件中的日期范围。

**抽取逻辑**：进度抽取提示词（`prompts.py` 第123行）给出示例格式：`"2026-05-12 ~ 2026-05-18"`。LLM从邮件内容中识别日期范围并格式化。

**钉钉映射**：同项目名称，字符串直接写入。

---

### 5. 项目进度 (`project_progress`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 项目进度 |
| 类型 | **progress** |
| 是否必填 | 是 |
| sourcePath | `analysis.进度抽取结果.项目进度` |

**数据来源**：邮件正文或附件中的进度指标（如"项目PC"、"项目进度"等）。

**抽取逻辑**：
1. 进度抽取提示词（`prompts.py` 第106-108行）明确要求：
   - 查找邮件正文/附件中的 `"项目PC"`、`"项目进度"` 等指标
   - 返回 **0~1之间的浮点数**
   - 百分比需转为小数（如 `"75%"` → `0.75`）
2. Excel附件预处理（`email_parser.py` 第452-453行）：单元格含百分比值时，`openpyxl` 读取的实际是 `0.75` 这样的浮点数，代码会将其乘以100格式化为 `"75.00%"` 字符串，因此LLM看到的是可读的百分比文字
3. LLM根据这些可读文字，再按提示词要求转回 0~1 的浮点数

**钉钉映射**：
- `artifact.analysis.进度抽取结果.项目进度`（浮点数，如 `0.75`）
- `dingtalk.py` 第269-278行 `normalize_cell_value()` 的 `progress` 分支：
  - 若为 `int`/`float` 则透传
  - 若为字符串则 `float()` 转换
- 最终直接写入钉钉的 **progress** 类型单元格，钉钉端按百分比显示

---

### 6. 里程碑 (`milestone`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 里程碑 |
| 类型 | text |
| 是否必填 | 是 |
| sourcePath | `analysis.进度抽取结果.里程碑` |

**数据来源**：邮件正文或附件中的里程碑信息。

**抽取逻辑**：进度抽取提示词（`prompts.py` 第125行）要求LLM返回 `"里程碑"` 字段，从邮件内容中提取关键里程碑文本。

**钉钉映射**：字符串直接写入。

---

### 7. 风险详情 (`risk_details`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 风险详情 |
| 类型 | text |
| 是否必填 | 否 |
| sourcePath | `analysis.风险抽取结果.风险详情` |

**数据来源**：
- 邮件正文中的"风险披露"段落
- Excel周报表中的"项目问题/风险"列
- 需求清单中的风险描述

**抽取逻辑**：
1. 风险抽取提示词（`prompts.py` 第133-160行）要求LLM返回 `"风险详情"` 数组
2. 每条风险包含6个键：

   | key | 含义 |
   |---|---|
   | 序号 | 数字序号 |
   | 风险类型 | 文本分类 |
   | 风险等级 | high/medium/low |
   | 风险描述 | 风险具体内容 |
   | 风险措施和最新进展 | 缓解措施 |
   | 风险责任人 | 责任人姓名 |
   | 计划解决日期 | 计划完成日期 |

3. 提示词强调只提取"显式"（explicit）风险，不臆造

**钉钉映射**：`dingtalk.py` 第236-248行 `format_risk_array_text()` 将数组格式化为多段文本：

```
1. 风险类型: xxx
风险等级: high/medium/low
风险描述: xxx
风险措施和最新进展: xxx
风险责任人: xxx
计划解决日期: xxx

2. 风险类型: xxx
风险等级: xxx
风险描述: xxx
...
```

每条风险之间用 `\n\n` 分隔，最终作为单一 **text** 单元格写入钉钉。

---

### 8. 项目整体进展 (`overall_progress`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 项目整体进展 |
| 类型 | text |
| 是否必填 | 否 |
| sourcePath | `analysis.进度抽取结果.项目整体进展` |

**数据来源**：Excel 周报附件中"项目周报"Sheet的 `"项目整体进展概况"` 字段。

**抽取逻辑**：
- 进度抽取提示词（`prompts.py` 第110行）明确要求：
  - 定位 Excel 周报附件（通常在"项目周报"Sheet）中的 `"项目整体进展概况"` 字段
  - **忠实提取原文**，不臆造或自行归纳
  - 保留段落/行分隔，确保钉钉表内可读性
  - 若附件中无该字段，则基于邮件正文/附件写简要总结
- LLM 输出的 `"项目整体进展"` 为 `进度抽取结果` 的直接子级（扁平结构，无嵌套）

**钉钉映射**：`artifact.analysis.进度抽取结果.项目整体进展` → `stringify_text_part()` → 直接文本写入。

---

### 9. 本周小结 (`weekly_summary`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 本周小结 |
| 类型 | text |
| 是否必填 | 否 |
| sourcePath | `analysis.进度抽取结果.本周小结` |

**数据来源**：邮件附件 Excel 周报中"本周小结"或"本周进展小结"字段。

**抽取逻辑**：
1. 进度抽取提示词（`prompts.py` 第111行）给出强约束规则：
   - 定位附件 Excel 周报（通常在"项目周报"Sheet）中的 `"本周小结"` 或 `"本周进展小结"` 字段
   - **忠实提取原文**，禁止改写、概括或臆造
   - 保持行分隔可读性
   - 每条编号项内的多个连续空格替换为单个逗号，避免钉钉表格中空格折叠导致内容粘连
   - 若附件中无此字段，则从邮件正文对应段落提取

**钉钉映射**：`artifact.analysis.进度抽取结果.本周小结` → `stringify_text_part()` → 直接文本写入。

---

### 10. 下周计划 (`next_week_plan`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 下周计划 |
| 类型 | text |
| 是否必填 | 否 |
| sourcePath | `analysis.进度抽取结果.下周计划` |

**数据来源**：邮件附件 Excel 周报中"下周计划"或"下周工作计划"字段。

**抽取逻辑**：
1. 进度抽取提示词（`prompts.py` 第112行）给出强约束规则：
   - 定位附件 Excel 周报（通常在"项目周报"Sheet）中的 `"下周计划"` 或 `"下周工作计划"` 字段
   - **忠实提取原文**，禁止改写、概括或臆造
   - 保持行分隔可读性
   - 每条编号项内的多个连续空格替换为单个逗号，避免钉钉表格中空格折叠导致内容粘连
   - 若附件中无此字段，则从邮件正文对应段落提取

**钉钉映射**：`artifact.analysis.进度抽取结果.下周计划` → `stringify_text_part()` → 直接文本写入。

---

### 11. 邮箱抽取日期 (`extract_date`)

| 属性 | 值 |
|---|---|
| 钉钉字段名 | 邮箱抽取日期 |
| 类型 | **date** |
| 是否必填 | 否 |
| sourcePath | `processed_at`（artifact顶层） |

**数据来源**：系统当前UTC时间，不来自邮件内容。

**抽取逻辑**：`service.py` 第87行在构建artifact时设置：
```python
"processed_at": datetime.now(timezone.utc).isoformat()
# 例: "2026-05-15T01:21:42.621229+00:00"
```

**钉钉映射**：`dingtalk.py` 第279-282行 `normalize_cell_value()` 的 `date` 分支：
- 截取前10个字符：`value[:10]`
- `"2026-05-15T01:21:42.621229+00:00"` → `"2026-05-15"`
- 写入钉钉的 **date** 类型单元格

---

## 值类型转换规则

`dingtalk.py` 的 `normalize_cell_value()` 函数（第259-283行）按字段类型执行转换：

| 钉钉类型 | 转换规则 | 代码行 |
|---|---|---|
| `text`（普通） | `stringify_text_part()`: str原样, dict/list→JSON字符串, 其他→str() | 第251-256行 |
| `text`（风险详情） | `format_risk_array_text()`: 6列×N条风险→编号多行文本 | 第236-248行 |
| `progress` | int/float透传, 字符串→float() | 第269-278行 |
| `number` | int/float透传, 数字字符串→int()/float() | 第263-268行 |
| `date` | 字符串截取前10字符 `value[:10]` | 第279-282行 |

---

## 同步去重机制

钉钉写入前执行两层去重：

1. **本地manifest去重**：`runs/<run_id>/sync/sync_manifest.json` 记录 `"base::{baseId}::table::{tableId}::message::{message_id}"` → 若状态为 `"synced"` 则跳过（`dingtalk.py` 第537-548行）
2. **远端钉钉去重**：通过 `query_records` 按 `message_id` 查询钉钉表（第570-585行），若已存在则跳过写入

只有两层都未匹配的记录才会真正调用 `create_records`（第587行）。

---

## runs文件夹中间结果与钉钉字段的对应关系

```
runs/<run_id>/mail/risk_json/<message_id>.json
  │
  ├─ .message_id          ──────→ 邮件标识 (用于去重和匹配)
  ├─ .processed_at        ──────→ 邮箱抽取日期 (截取YYYY-MM-DD)
  │
  └─ .analysis.进度抽取结果
       ├─ 一致性分析        ──────→ 不直接写入钉钉（仅保留在artifact中供审计）
       ├─ 项目整体进展      ──────→ 项目整体进展
       ├─ 项目名称         ──────→ 项目名称
       ├─ 项目编号         ──────→ 项目编号
       ├─ 项目周报周期      ──────→ 项目周报周期
       ├─ 项目进度         ──────→ 项目进度 (浮点数)
       ├─ 里程碑           ──────→ 里程碑
       ├─ 本周小结         ──────→ 本周小结
       ├─ 下周计划         ──────→ 下周计划
       └─ 原文抽取         ──────→ 不直接写入钉钉（仅保留在artifact中供审计）
  │
  └─ .analysis.风险抽取结果
       └─ 风险详情[]       ──────→ 风险详情 (格式化为多行文本)
```

---

## 进度抽取结构变更记录（2026-05-15）

原结构（已废弃）：
```json
{
  "原文抽取": [...],
  "分析结果": {
    "综合总结": "...",
    "一致性分析": "..."
  },
  "项目名称": "...",
  ...
}
```

新结构（当前）：
```json
{
  "原文抽取": [...],
  "一致性分析": "...",
  "项目整体进展": "...",
  "项目名称": "...",
  ...
}
```

变更要点：
- `分析结果` 嵌套层已移除，`一致性分析` 提升为 `进度抽取结果` 的直接子级
- `综合总结` 已删除，新增 `项目整体进展` 字段替代
- `项目整体进展` 的数据来源从 LLM 自由归纳改为 Excel 附件中"项目整体进展概况"原文提取
- `dingtalk.py` 中 `SUMMARY_KEY` 常量和 `format_progress_analysis_text()` 函数已删除（死代码清理）

### 抽取约束强化（2026-05-15）

变更要点：
- `本周小结` 和 `下周计划` 的提示词从无约束改为强约束，与 `项目整体进展` 一致
- `本周小结`：定位附件中"本周小结"或"本周进展小结"字段，忠实提取原文，禁止改写/概括/臆造，保持行分隔，多空格替为逗号，无此字段时从邮件正文提取
- `下周计划`：定位附件中"下周计划"或"下周工作计划"字段，忠实提取原文，禁止改写/概括/臆造，保持行分隔，多空格替为逗号，无此字段时从邮件正文提取
- 变更原因：同一封邮件两次抽取结果不一致，根因为 LLM 对这两个字段的自由改写空间过大