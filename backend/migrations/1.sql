-- ============================================================
-- GeoPaperAgent 数据库初始化脚本
-- 数据库：PostgreSQL
-- 核心表：paper、section、text_chunk、image_chunk、table_chunk
-- ============================================================


-- 1. 创建并切换数据库（使用 psql 执行本文件）
CREATE DATABASE "geopaperagent"
    WITH
    ENCODING = 'UTF8'
    TEMPLATE = template0;

COMMENT ON DATABASE "geopaperagent"
    IS 'GeoPaperAgent 地质论文知识库数据库';

\connect "geopaperagent"


-- 2. 论文表
CREATE TABLE paper (
    id              BIGSERIAL PRIMARY KEY,
    paper_uid       VARCHAR(64) NOT NULL UNIQUE,

    title           TEXT NOT NULL,
    title_en        TEXT,
    abstract        TEXT,

    authors         JSONB DEFAULT '[]'::jsonb,
    keywords        JSONB DEFAULT '[]'::jsonb,
    affiliations    JSONB DEFAULT '[]'::jsonb,

    doi             VARCHAR(255),
    journal         VARCHAR(255),
    volume          VARCHAR(50),
    issue           VARCHAR(50),

    publish_year    SMALLINT,
    publish_date    DATE,
    language        VARCHAR(20),

    source_url      TEXT,
    pdf_path        TEXT,
    markdown_path   TEXT,
    document_json_path TEXT,
    ingestion_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    page_count      INTEGER,
    file_metadata   JSONB DEFAULT '{}'::jsonb,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE paper IS '论文基础信息及文件位置';


-- 3. 章节表
CREATE TABLE section (
    id              BIGSERIAL PRIMARY KEY,
    section_uid     VARCHAR(100) NOT NULL UNIQUE,

    paper_id        BIGINT NOT NULL
        REFERENCES paper(id)
        ON DELETE CASCADE,

    parent_id       BIGINT
        REFERENCES section(id)
        ON DELETE CASCADE,

    section_number  VARCHAR(50),
    title           TEXT NOT NULL,
    level           SMALLINT NOT NULL,
    sort_order      INTEGER NOT NULL,
    section_path    JSONB DEFAULT '[]'::jsonb,
    raw_text        TEXT,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE section IS '论文章节及其层级关系';


-- 4. 文本块表
CREATE TABLE text_chunk (
    id                  BIGSERIAL PRIMARY KEY,
    chunk_uid           VARCHAR(100) NOT NULL UNIQUE,

    paper_id            BIGINT NOT NULL
        REFERENCES paper(id)
        ON DELETE CASCADE,

    section_id          BIGINT NOT NULL
        REFERENCES section(id)
        ON DELETE CASCADE,

    chunk_index         INTEGER NOT NULL,
    content             TEXT NOT NULL,
    char_count          INTEGER,

    previous_chunk_id   BIGINT
        REFERENCES text_chunk(id)
        ON DELETE SET NULL,

    next_chunk_id       BIGINT
        REFERENCES text_chunk(id)
        ON DELETE SET NULL,

    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_text_chunk_section_index
        UNIQUE (section_id, chunk_index)
);

COMMENT ON TABLE text_chunk IS '论文正文切分后的文本块';


-- 5. 图片块表
CREATE TABLE image_chunk (
    id                  BIGSERIAL PRIMARY KEY,
    chunk_uid           VARCHAR(100) NOT NULL UNIQUE,

    paper_id            BIGINT NOT NULL
        REFERENCES paper(id)
        ON DELETE CASCADE,

    section_id          BIGINT NOT NULL
        REFERENCES section(id)
        ON DELETE CASCADE,

    image_index         INTEGER,
    figure_number       VARCHAR(50),
    image_path          TEXT NOT NULL,
    caption             TEXT,
    image_type          VARCHAR(100),
    description         TEXT,
    structured_data     JSONB DEFAULT '{}'::jsonb,
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE image_chunk IS '论文图片及其解析信息';


-- 6. 表格块表
CREATE TABLE table_chunk (
    id                  BIGSERIAL PRIMARY KEY,
    chunk_uid           VARCHAR(100) NOT NULL UNIQUE,

    paper_id            BIGINT NOT NULL
        REFERENCES paper(id)
        ON DELETE CASCADE,

    section_id          BIGINT NOT NULL
        REFERENCES section(id)
        ON DELETE CASCADE,

    table_index         INTEGER,
    table_number        VARCHAR(50),
    caption             TEXT,
    page_number         INTEGER,
    bbox                JSONB,
    table_html          TEXT,
    structured_data     JSONB DEFAULT '{}'::jsonb,
    description         TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE table_chunk IS '论文表格及其结构化解析信息';


-- 7. 关系及检索索引
CREATE INDEX idx_section_paper_id
    ON section (paper_id);

CREATE INDEX idx_section_parent_id
    ON section (parent_id);

CREATE INDEX idx_section_paper_sort
    ON section (paper_id, sort_order);

CREATE INDEX idx_text_chunk_paper_id
    ON text_chunk (paper_id);

CREATE INDEX idx_text_chunk_section_id
    ON text_chunk (section_id);

CREATE INDEX idx_image_chunk_paper_id
    ON image_chunk (paper_id);

CREATE INDEX idx_image_chunk_section_id
    ON image_chunk (section_id);

CREATE INDEX idx_table_chunk_paper_id
    ON table_chunk (paper_id);

CREATE INDEX idx_table_chunk_section_id
    ON table_chunk (section_id);

CREATE INDEX idx_paper_doi
    ON paper (doi);

CREATE INDEX idx_paper_publish_year
    ON paper (publish_year);

CREATE INDEX idx_paper_ingestion_status
    ON paper (ingestion_status);

CREATE INDEX idx_text_chunk_content_fts
    ON text_chunk USING GIN (to_tsvector('simple', content));

CREATE INDEX idx_paper_keywords_gin
    ON paper USING GIN (keywords);
