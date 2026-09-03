-- GeoPaperAgent 解析入库管线增量迁移。
-- 本文件为已经执行过 1.sql 的数据库补充文档树地址、流程状态和错误信息字段。

ALTER TABLE paper
    ADD COLUMN IF NOT EXISTS document_json_path TEXT,
    ADD COLUMN IF NOT EXISTS ingestion_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS error_message TEXT;

CREATE INDEX IF NOT EXISTS idx_paper_ingestion_status
    ON paper (ingestion_status);

COMMENT ON COLUMN paper.document_json_path IS '章节树和多模态 Chunk JSON 的 MinIO 地址';
COMMENT ON COLUMN paper.ingestion_status IS '论文解析入库流程的当前状态';
COMMENT ON COLUMN paper.error_message IS '最近一次解析入库失败的错误信息';
