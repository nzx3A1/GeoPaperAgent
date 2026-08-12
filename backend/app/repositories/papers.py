import json
from pathlib import Path

from app.infrastructure.database import connection
from app.parsers.mineru_output import ParsedDocument, split_text
from app.schemas.paper import PaperMetadata


async def ingest_document(paper_uid: str, pdf_path: Path | None, output_root: Path, document: ParsedDocument,
                          metadata: PaperMetadata, chunk_size: int, overlap: int) -> int:
    title = metadata.title or document.title
    async with connection() as conn, conn.transaction():
        old_id = await conn.fetchval("SELECT id FROM paper WHERE paper_uid=$1", paper_uid)
        if old_id:
            await conn.execute("DELETE FROM paper WHERE id=$1", old_id)
        paper_id = await conn.fetchval(
            """INSERT INTO paper (paper_uid,title,authors,keywords,doi,journal,publish_year,language,source_url,
               pdf_path,markdown_path,mineru_output_path,page_count,geological_metadata,file_metadata)
               VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb) RETURNING id""",
            paper_uid, title, json.dumps(metadata.authors, ensure_ascii=False),
            json.dumps(metadata.keywords, ensure_ascii=False), metadata.doi, metadata.journal, metadata.publish_year,
            metadata.language, metadata.source_url, str(pdf_path) if pdf_path else None, str(document.markdown_path),
            str(output_root), document.page_count, json.dumps(metadata.geological_metadata, ensure_ascii=False),
            json.dumps({}, ensure_ascii=False),
        )
        sections: list[tuple[int, int, list[str]]] = []
        current_section_id: int | None = None
        current_path: list[str] = []
        text_index: dict[int, int] = {}
        media_index = {"image": 0, "table": 0}
        for order, block in enumerate(document.blocks):
            if block.kind == "heading":
                level = block.level or 1
                while sections and sections[-1][0] >= level:
                    sections.pop()
                parent_id = sections[-1][1] if sections else None
                current_path = (sections[-1][2] if sections else []) + [block.text]
                current_section_id = await conn.fetchval(
                    """INSERT INTO section(
                           section_uid,paper_id,parent_id,title,level,sort_order,start_page,end_page,section_path
                       )
                       VALUES($1,$2,$3,$4,$5,$6,$7,$7,$8::jsonb) RETURNING id""",
                    f"{paper_uid}-s{order}", paper_id, parent_id, block.text, level, order, block.page,
                    json.dumps(current_path, ensure_ascii=False),
                )
                sections.append((level, current_section_id, current_path))
                continue
            if current_section_id is None:
                current_path = ["正文"]
                current_section_id = await conn.fetchval(
                    """INSERT INTO section(section_uid,paper_id,title,level,sort_order,section_path)
                       VALUES($1,$2,'正文',1,0,$3::jsonb) RETURNING id""",
                    f"{paper_uid}-root", paper_id, json.dumps(current_path, ensure_ascii=False),
                )
            if block.kind == "text":
                for content in split_text(block.text, chunk_size, overlap):
                    index = text_index.get(current_section_id, 0)
                    await conn.execute(
                        """INSERT INTO text_chunk(chunk_uid,paper_id,section_id,chunk_index,content,char_count,metadata)
                           VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)""",
                        f"{paper_uid}-t{order}-{index}", paper_id, current_section_id, index, content, len(content),
                        json.dumps({"page": block.page, "section_path": current_path}, ensure_ascii=False),
                    )
                    text_index[current_section_id] = index + 1
            elif block.kind == "image" and block.path:
                media_index["image"] += 1
                await conn.execute(
                    """INSERT INTO image_chunk(chunk_uid,paper_id,section_id,image_index,image_path,caption,metadata)
                       VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)""",
                    f"{paper_uid}-i{media_index['image']}", paper_id, current_section_id, media_index["image"],
                    block.path, block.caption, json.dumps({"page": block.page}, ensure_ascii=False),
                )
            elif block.kind == "table":
                media_index["table"] += 1
                await conn.execute(
                    """INSERT INTO table_chunk(
                           chunk_uid,paper_id,section_id,table_index,caption,page_number,bbox,table_html,metadata
                       )
                       VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb)""",
                    f"{paper_uid}-tb{media_index['table']}", paper_id, current_section_id, media_index["table"],
                    block.caption, block.page, json.dumps(block.bbox), block.text, json.dumps({}, ensure_ascii=False),
                )
        return int(paper_id)


async def get_paper(paper_uid: str) -> dict | None:
    async with connection() as conn:
        row = await conn.fetchrow(
            """SELECT p.*, (SELECT count(*) FROM section s WHERE s.paper_id=p.id) section_count,
               (SELECT count(*) FROM text_chunk c WHERE c.paper_id=p.id) text_chunk_count,
               (SELECT count(*) FROM image_chunk c WHERE c.paper_id=p.id) image_count,
               (SELECT count(*) FROM table_chunk c WHERE c.paper_id=p.id) table_count
               FROM paper p WHERE paper_uid=$1""",
            paper_uid,
        )
        return dict(row) if row else None
