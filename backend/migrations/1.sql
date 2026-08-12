-- ============================================================
-- GeoPaperAgent 第一阶段数据库初始化脚本
-- 数据库：geopaperAgent
-- 数据库类型：PostgreSQL
-- 向量扩展：pgvector
--
-- 核心表：
--   1. paper        论文表
--   2. section      章节表
--   3. text_chunk   文本块表
--   4. image_chunk  图片块表
--   5. table_chunk  表格块表
--
-- 注意：
--   1. PostgreSQL 默认会把未加双引号的标识符转为小写。
--      为保留数据库名 geopaperAgent 的大小写，本脚本使用双引号。
--   2. 执行前需要 PostgreSQL 已安装 pgvector 扩展。
--   3. VECTOR(1024) 按当前设计预留 1024 维向量，
--      如果后续更换 Embedding 模型，需要与模型输出维度保持一致。
-- ============================================================


-- ============================================================
-- 1. 创建数据库
-- ============================================================

CREATE DATABASE "geopaperAgent"
    WITH
    ENCODING = 'UTF8'
    TEMPLATE = template0;

COMMENT ON DATABASE "geopaperAgent"
    IS 'GeoPaperAgent 地质论文知识库数据库';


-- 切换到新数据库。
-- 本命令适用于 psql 执行 SQL 文件。
\connect "geopaperAgent"


-- ============================================================
-- 2. 启用 pgvector
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;


-- ============================================================
-- 3. paper：论文表
-- ============================================================

CREATE TABLE paper (
    id                  BIGSERIAL PRIMARY KEY,
    paper_uid           VARCHAR(64) NOT NULL UNIQUE,

    title               TEXT NOT NULL,
    title_en            TEXT,
    abstract            TEXT,

    authors             JSONB DEFAULT '[]'::jsonb,
    keywords            JSONB DEFAULT '[]'::jsonb,
    affiliations        JSONB DEFAULT '[]'::jsonb,

    doi                 VARCHAR(255),
    journal             VARCHAR(255),
    volume              VARCHAR(50),
    issue               VARCHAR(50),
    pages               VARCHAR(50),

    publish_year        SMALLINT,
    publish_date        DATE,
    language            VARCHAR(20),

    source_url          TEXT,

    pdf_path            TEXT,
    markdown_path       TEXT,
    mineru_output_path  TEXT,
    page_count          INTEGER,

    geological_metadata JSONB DEFAULT '{}'::jsonb,
    file_metadata       JSONB DEFAULT '{}'::jsonb,

    embedding           VECTOR(1024),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE paper IS '论文主表，保存论文基础信息、文件位置、地质元数据及论文级向量';

COMMENT ON COLUMN paper.id IS '数据库自增主键';
COMMENT ON COLUMN paper.paper_uid IS '系统内部论文唯一编号';
COMMENT ON COLUMN paper.title IS '论文原始标题';
COMMENT ON COLUMN paper.title_en IS '论文英文标题';
COMMENT ON COLUMN paper.abstract IS '论文摘要';
COMMENT ON COLUMN paper.authors IS '作者信息，JSON 数组';
COMMENT ON COLUMN paper.keywords IS '论文关键词，JSON 数组';
COMMENT ON COLUMN paper.affiliations IS '作者单位信息，JSON 数组';
COMMENT ON COLUMN paper.doi IS '论文 DOI';
COMMENT ON COLUMN paper.journal IS '发表期刊名称';
COMMENT ON COLUMN paper.volume IS '期刊卷号';
COMMENT ON COLUMN paper.issue IS '期刊期号';
COMMENT ON COLUMN paper.pages IS '论文在期刊中的页码范围';
COMMENT ON COLUMN paper.publish_year IS '发表年份';
COMMENT ON COLUMN paper.publish_date IS '发表日期';
COMMENT ON COLUMN paper.language IS '论文语言，例如 zh、en';
COMMENT ON COLUMN paper.source_url IS '论文原始网页或下载来源地址';
COMMENT ON COLUMN paper.pdf_path IS '原始 PDF 文件存储路径';
COMMENT ON COLUMN paper.markdown_path IS 'MinerU 解析后的 Markdown 文件路径';
COMMENT ON COLUMN paper.mineru_output_path IS 'MinerU 完整解析结果目录';
COMMENT ON COLUMN paper.page_count IS '论文 PDF 总页数';
COMMENT ON COLUMN paper.geological_metadata IS '地质领域元数据，如盆地、地层、层段、研究主题等';
COMMENT ON COLUMN paper.file_metadata IS '文件相关扩展信息，如文件大小、哈希值等';
COMMENT ON COLUMN paper.embedding IS '论文级语义向量，用于论文级向量检索';
COMMENT ON COLUMN paper.created_at IS '记录创建时间';
COMMENT ON COLUMN paper.updated_at IS '记录最后更新时间';


-- ============================================================
-- 4. section：章节表
-- ============================================================

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

    start_page      INTEGER,
    end_page        INTEGER,

    section_path    JSONB DEFAULT '[]'::jsonb,

    raw_text        TEXT,
    summary         TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE section IS '论文章节表，通过 parent_id 自关联构建章节树';

COMMENT ON COLUMN section.id IS '数据库自增主键';
COMMENT ON COLUMN section.section_uid IS '章节唯一编号';
COMMENT ON COLUMN section.paper_id IS '所属论文 ID，关联 paper.id';
COMMENT ON COLUMN section.parent_id IS '父章节 ID，关联 section.id；一级章节为空';
COMMENT ON COLUMN section.section_number IS '论文章节编号，如 3、3.2、3.2.1';
COMMENT ON COLUMN section.title IS '章节标题';
COMMENT ON COLUMN section.level IS '章节层级，例如一级标题为 1，二级标题为 2';
COMMENT ON COLUMN section.sort_order IS '章节在整篇论文中的排序序号';
COMMENT ON COLUMN section.start_page IS '章节起始页码';
COMMENT ON COLUMN section.end_page IS '章节结束页码';
COMMENT ON COLUMN section.section_path IS '完整章节层级路径，JSON 数组';
COMMENT ON COLUMN section.raw_text IS '章节完整原始文本';
COMMENT ON COLUMN section.summary IS '章节摘要';
COMMENT ON COLUMN section.created_at IS '记录创建时间';


-- ============================================================
-- 5. text_chunk：文本块表
-- ============================================================

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

    embedding           VECTOR(1024),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_text_chunk_section_index
        UNIQUE(section_id, chunk_index)
);

COMMENT ON TABLE text_chunk IS '文本 Chunk 表，保存论文正文切分后的文本块及其向量';

COMMENT ON COLUMN text_chunk.id IS '数据库自增主键';
COMMENT ON COLUMN text_chunk.chunk_uid IS '文本 Chunk 唯一编号';
COMMENT ON COLUMN text_chunk.paper_id IS '所属论文 ID，关联 paper.id';
COMMENT ON COLUMN text_chunk.section_id IS '所属章节 ID，关联 section.id';
COMMENT ON COLUMN text_chunk.chunk_index IS 'Chunk 在当前章节中的顺序';
COMMENT ON COLUMN text_chunk.content IS 'Chunk 文本内容';
COMMENT ON COLUMN text_chunk.char_count IS 'Chunk 字符数量';
COMMENT ON COLUMN text_chunk.previous_chunk_id IS '上一个文本 Chunk ID';
COMMENT ON COLUMN text_chunk.next_chunk_id IS '下一个文本 Chunk ID';
COMMENT ON COLUMN text_chunk.metadata IS '文本块扩展信息，JSON 对象';
COMMENT ON COLUMN text_chunk.embedding IS '文本 Chunk 语义向量';
COMMENT ON COLUMN text_chunk.created_at IS '记录创建时间';


-- ============================================================
-- 6. image_chunk：图片块表
-- ============================================================

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

    embedding           VECTOR(1024),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE image_chunk IS '图片 Chunk 表，保存论文图片、图题、图片语义描述、结构化解析结果及向量';

COMMENT ON COLUMN image_chunk.id IS '数据库自增主键';
COMMENT ON COLUMN image_chunk.chunk_uid IS '图片 Chunk 唯一编号';
COMMENT ON COLUMN image_chunk.paper_id IS '所属论文 ID，关联 paper.id';
COMMENT ON COLUMN image_chunk.section_id IS '所属章节 ID，关联 section.id';
COMMENT ON COLUMN image_chunk.image_index IS '图片在当前论文或章节中的顺序';
COMMENT ON COLUMN image_chunk.figure_number IS '论文中的图号，如 Fig.5、图5';
COMMENT ON COLUMN image_chunk.image_path IS '图片文件存储路径';
COMMENT ON COLUMN image_chunk.caption IS '图片标题或图注';
COMMENT ON COLUMN image_chunk.image_type IS '图片类型，如地层柱状图、沉积相图、地质剖面图等';
COMMENT ON COLUMN image_chunk.description IS 'VLM 或解析模型生成的图片语义描述';
COMMENT ON COLUMN image_chunk.structured_data IS '图片结构化解析结果，JSON 对象';
COMMENT ON COLUMN image_chunk.metadata IS '图片块其他扩展信息，JSON 对象';
COMMENT ON COLUMN image_chunk.embedding IS '图片语义向量';
COMMENT ON COLUMN image_chunk.created_at IS '记录创建时间';


-- ============================================================
-- 7. table_chunk：表格块表
-- ============================================================

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

    embedding           VECTOR(1024),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE table_chunk IS '表格 Chunk 表，保存论文表格内容、结构化数据、语义描述及向量';

COMMENT ON COLUMN table_chunk.id IS '数据库自增主键';
COMMENT ON COLUMN table_chunk.chunk_uid IS '表格 Chunk 唯一编号';
COMMENT ON COLUMN table_chunk.paper_id IS '所属论文 ID，关联 paper.id';
COMMENT ON COLUMN table_chunk.section_id IS '所属章节 ID，关联 section.id';
COMMENT ON COLUMN table_chunk.table_index IS '表格在当前论文或章节中的顺序';
COMMENT ON COLUMN table_chunk.table_number IS '论文中的表号，如 Table 2、表2';
COMMENT ON COLUMN table_chunk.caption IS '表格标题或表注';
COMMENT ON COLUMN table_chunk.page_number IS '表格所在 PDF 页码';
COMMENT ON COLUMN table_chunk.bbox IS '表格在 PDF 页面中的位置范围，JSON 对象';
COMMENT ON COLUMN table_chunk.table_html IS '表格 HTML 内容';
COMMENT ON COLUMN table_chunk.structured_data IS '表格结构化数据，JSON 对象';
COMMENT ON COLUMN table_chunk.description IS '表格内容的自然语言语义描述';
COMMENT ON COLUMN table_chunk.metadata IS '表格块其他扩展信息，JSON 对象';
COMMENT ON COLUMN table_chunk.embedding IS '表格语义向量';
COMMENT ON COLUMN table_chunk.created_at IS '记录创建时间';


-- ============================================================
-- 8. 普通关系索引
-- ============================================================

CREATE INDEX idx_section_paper_id
    ON section(paper_id);

CREATE INDEX idx_section_parent_id
    ON section(parent_id);

CREATE INDEX idx_section_paper_sort
    ON section(paper_id, sort_order);

CREATE INDEX idx_text_chunk_paper_id
    ON text_chunk(paper_id);

CREATE INDEX idx_text_chunk_section_id
    ON text_chunk(section_id);

CREATE INDEX idx_image_chunk_paper_id
    ON image_chunk(paper_id);

CREATE INDEX idx_image_chunk_section_id
    ON image_chunk(section_id);

CREATE INDEX idx_table_chunk_paper_id
    ON table_chunk(paper_id);

CREATE INDEX idx_table_chunk_section_id
    ON table_chunk(section_id);

CREATE INDEX idx_paper_doi
    ON paper(doi);

CREATE INDEX idx_paper_publish_year
    ON paper(publish_year);

CREATE INDEX idx_text_chunk_content_fts
    ON text_chunk USING GIN(to_tsvector('simple', content));


-- ============================================================
-- 9. JSONB 索引
-- ============================================================

CREATE INDEX idx_paper_keywords_gin
    ON paper USING GIN(keywords);

CREATE INDEX idx_paper_geological_metadata_gin
    ON paper USING GIN(geological_metadata);


-- ============================================================
-- 10. pgvector HNSW 向量索引
-- ============================================================

CREATE INDEX idx_paper_embedding_hnsw
    ON paper
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX idx_text_chunk_embedding_hnsw
    ON text_chunk
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX idx_image_chunk_embedding_hnsw
    ON image_chunk
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX idx_table_chunk_embedding_hnsw
    ON table_chunk
    USING hnsw (embedding vector_cosine_ops);


-- ============================================================
-- 初始化完成
-- ============================================================
