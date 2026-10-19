"""知识库:文本分块与向量检索(M2)。

向量存 JSON、Python 余弦匹配,零额外组件;数据量大时可平滑迁移 pgvector。
"""

import math

from sqlalchemy.orm import Session

from ..models import KbChunk, NovelKbLink
from .gateway import embed_texts

CHUNK_MAX_CHARS = 600
CHUNK_OVERLAP = 60


def chunk_text(text: str, max_len: int = CHUNK_MAX_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按段落聚合分块:优先在段落边界切,超长段落内部滑动窗口。"""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > max_len:
            # 超长段落先落盘缓冲,再滑动窗口切
            if buf:
                chunks.append(buf)
                buf = ""
            step = max_len - overlap
            for i in range(0, len(para), step):
                piece = para[i : i + max_len]
                if len(piece) >= overlap:
                    chunks.append(piece)
            continue
        if len(buf) + len(para) + 1 > max_len:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def novel_kb_ids(db: Session, novel_id: int) -> list[int]:
    return [row.kb_id for row in db.query(NovelKbLink).filter_by(novel_id=novel_id).all()]


async def retrieve(
    db: Session,
    user_id: int,
    novel_id: int,
    query: str,
    top_k: int = 6,
) -> list[dict]:
    """在书挂载的知识库中检索相关片段,带来源与分数。"""
    kb_ids = novel_kb_ids(db, novel_id)
    if not kb_ids or not query.strip():
        return []
    chunks = (
        db.query(KbChunk)
        .filter(KbChunk.kb_id.in_(kb_ids), KbChunk.embedding.isnot(None))
        .limit(5000)
        .all()
    )
    if not chunks:
        return []
    qvec = (await embed_texts(db, user_id, [query[:1000]]))[0]
    scored = [(c, _cosine(qvec, c.embedding)) for c in chunks]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [
        {"content": c.content, "kb_id": c.kb_id, "document_id": c.document_id, "score": round(s, 4)}
        for c, s in scored[:top_k]
        if s > 0.15
    ]
