# GeoPaperAgent backend

当前解析管线会依次完成：SSH/SFTP 下载 PDF、上传 PDF 到 MinIO、创建 Paper 初始记录、
调用 MinerU 得到 Markdown、上传 Markdown 与图片等解析产物、解析章节树和首页元数据、
构建文本/公式/图片/表格 Chunk，最后在单个 PostgreSQL 事务中写入 Paper、Section 和 Chunk。

## 初始化

```powershell
cd backend
uv sync --extra dev
Copy-Item .env.example .env
psql -U postgresql -f migrations/1.sql
```

如果数据库已经执行过旧版 `1.sql`，只需再执行：

```powershell
psql -U postgresql -d geopaperagent -f migrations/2_ingestion_pipeline.sql
```

`.env` 至少需要有效的 `DATABASE_URL`、`MINIO_*`、`SSH_*` 和 `MINERU_API_KEY`。
默认会调用 `app/core/agent/model/llmClient.py` 提取作者、单位、期刊、卷期、DOI 等首页元数据，
因此还应配置 `LLM_*`；暂时不使用 LLM 时可以传 `--skip-llm`。

## 运行完整流程

在 `backend` 目录执行以下命令，默认处理远程目录排序后的 3 篇 PDF：

```powershell
.venv\Scripts\python.exe -m app.parsers
```

常用参数：

```powershell
.venv\Scripts\python.exe -m app.parsers `
  --count 3 `
  --remote-directory /house1/KUN_QT_DATA/PDF_all `
  --recursive
```

- `--overwrite`：重新下载本地已有 PDF。
- `--force-mineru`：忽略已存在的 `full.md`，重新提交 MinerU。
- `--skip-llm`：只用规则提取首页信息。
- `--local-pdf-directory`、`--mineru-output`、`--output`：覆盖各阶段本地目录。

单篇失败不会中止另外两篇；最终结果写入
`output/ingestion/stage_05_ingestion_summary.json`，Paper 的 `ingestion_status` 和
`error_message` 会同步记录状态。

## 分阶段运行

```powershell
.venv\Scripts\python.exe app/parsers/AsshDownload.py --count 3
.venv\Scripts\python.exe app/parsers/BminerU_pipeline.py
.venv\Scripts\python.exe app/parsers/CMarkdownParser.py --use-llm-basic-info
.venv\Scripts\python.exe app/parsers/Ddocument_tree_builder.py
.venv\Scripts\python.exe app/parsers/EwriteSQL.py --input output/stage_04_document_tree.json
```

数据库中的 `pdf_path`、`markdown_path`、`document_json_path` 保存 `minio://bucket/object` 地址；
图片 Chunk 的 `image_path` 也会在总流程中替换为 MinIO 地址。公式使用 text_chunk 表保存，
并在 `metadata.modality` 中标记为 `formula`。
