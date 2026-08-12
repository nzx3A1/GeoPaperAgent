# GeoPaperAgent backend

第一阶段实现 MinerU PDF 解析、章节/文本/图片/表格重建和 PostgreSQL 入库。

```powershell
cd backend
.venv\Scripts\pip install -e ".[dev]"
psql -U postgres -f migrations/1.sql
.venv\Scripts\uvicorn app.main:app --reload
```

接口文档启动后位于 `http://127.0.0.1:8000/docs`。核心接口：

- `POST /api/v1/papers/ingestions/upload`：multipart 上传 PDF；`metadata` 为 JSON 字符串。
- `POST /api/v1/papers/ingestions/url`：提交公网 PDF URL。
- `GET /api/v1/papers/ingestions/{job_id}`：查询 MinerU 和入库进度。
- `POST /api/v1/papers/ingestions/{job_id}/retry`：重试失败任务。
- `GET /api/v1/papers/{paper_uid}`：读取论文及各类记录数量。

`.env` 必须配置 `minerU_API_KEY` 和 `DATABASE_URL`。后台任务目前运行在 API 进程内，生产环境可将
`run_job` 平移至 Celery；接口和核心服务无需改变。
