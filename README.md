# RAG Citation Collection Tool

這個專案用來把 Ahrefs 匯出的引用網址整理成可供 RAG 系統使用的 Citation 資料。流程包含建立案例、透過 Firecrawl 擷取網頁、清理 Markdown，以及處理無法自動擷取的平台來源。

## 系統流程

```text
Ahrefs UTF-16 TSV
        ↓
建立 Case JSON
        ↓
Firecrawl 擷取 Citation
        ↓
清理 Markdown 與品質檢查
        ↓
RAG 可用資料 / 人工擷取佇列
```

目前資料集包含 14 個 cases、87 筆 citation 關係。Firecrawl 無法處理的 Threads、Facebook、LinkedIn Company Pages、Reddit 等來源會列入人工擷取佇列。

## 專案結構

```text
config/
  cleaning_rules.json         Citation 清理規則
data/
  raw/                        Ahrefs 原始匯出
  cases/                      每筆查詢對應的 Case JSON
  citation_resources/         Firecrawl 原始擷取結果
  cleaned_citations/          清理後的 Citation
  manual_inputs/              人工擷取內容（預設不納入版控）
reports/
  crawl_batch_report.json     批次擷取報告
  manual_capture_queue.csv    待人工擷取清單
scripts/
  build_cases.py              從 Ahrefs 匯出建立 cases
  crawl_case.py               擷取單一 case
  crawl_all_cases.py          批次擷取 cases
  clean_citations.py          清理 Citation Markdown
tests/                        自動化測試
```

## 環境需求

- Python 3.10 以上
- Firecrawl API key（只有擷取階段需要）
- pytest（只有執行測試時需要）

在專案根目錄建立 `.env`：

```dotenv
FIRECRAWL_API_KEY=your_api_key_here
```

`.env` 已由 `.gitignore` 排除，請勿提交 API key。

## 使用方式

所有命令皆從專案根目錄執行。

### 1. 建立 cases

輸入檔必須是 Ahrefs 匯出的 UTF-16 TSV；專案沿用 `.csv` 副檔名。

```powershell
python scripts/build_cases.py
```

預設讀取 `data/raw/ahrefs.csv`，輸出至 `data/cases/`，並產生 `cases_index.json` 與 `build_report.json`。

自訂路徑：

```powershell
python scripts/build_cases.py --input path/to/ahrefs.csv --output-dir data/cases
```

### 2. 預覽批次擷取

建議先以 dry run 檢查將要處理的 cases：

```powershell
python scripts/crawl_all_cases.py --dry-run
```

### 3. 擷取 Citation

批次擷取全部尚未完成的 cases：

```powershell
python scripts/crawl_all_cases.py
```

重新處理未完整成功的 cases：

```powershell
python scripts/crawl_all_cases.py --retry-incomplete
```

只處理指定 case：

```powershell
python scripts/crawl_all_cases.py --case case_001
```

也可以直接執行單一 case：

```powershell
python scripts/crawl_case.py --case data/cases/case_001.json
```

擷取結果會寫入 `data/citation_resources/<case_id>/`；批次摘要會寫入 `reports/crawl_batch_report.json`。

### 4. 清理 Citation

```powershell
python scripts/clean_citations.py `
  --input-dir data/citation_resources/case_001 `
  --output-dir data/cleaned_citations/case_001 `
  --citation-ids citation_001 citation_002
```

清理行為由 `config/cleaning_rules.json` 控制，包括通用雜訊、網站專用規則、最低內容長度及人工複核門檻。每個 case 的輸出目錄會包含 `cleaning_manifest.json`。

## Manual Capture

部分引用網址（例如 Threads、Facebook、LinkedIn Company Pages、Reddit）因平台限制，無法由 Firecrawl 自動收集。

待處理項目記錄於：

```text
reports/manual_capture_queue.csv
```

人工擷取時，請複製以下範本並填入內容：

```text
data/manual_inputs/example_template.md
```

請保留原始段落與換行，排除導覽列、Cookie banner、頁尾、相關文章、留言及廣告。完成後可在佇列的 `capture_method` 與 `notes` 欄位記錄處理方式及補充資訊。

`data/manual_inputs/*.md` 原始人工擷取內容會由 `.gitignore` 排除；只有 `example_template.md` 與 `.gitkeep` 可納入版本控制。

## 測試

```powershell
python -m pytest
```

目前測試涵蓋 case 建立與 Citation 清理邏輯。

## 主要輸出格式

每份 Citation resource 至少包含：

- `case_id`
- `citation_id`
- `source_url`
- `status`
- `firecrawl`（成功時的擷取資料）
- `error`（失敗時的錯誤資訊）

`status != success` 的項目應加入 `reports/manual_capture_queue.csv`，供後續人工處理。
