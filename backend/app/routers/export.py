"""M4 导出:整书 TXT / EPUB。"""

import io
import zipfile
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Chapter, ReviewableFilter, User
from ..routers.novels import get_owned_novel
from ..security import get_current_user

router = APIRouter(prefix="/novels/{novel_id}/export", tags=["export"])


def _chapters(db: Session, novel_id: int, scope: str):
    q = db.query(Chapter).filter(Chapter.novel_id == novel_id)
    if scope != "all":
        q = q.filter(Chapter.status.in_(ReviewableFilter))
    return q.order_by(Chapter.sort, Chapter.id).all()


def _filename_header(name: str) -> str:
    from urllib.parse import quote

    return f"attachment; filename*=UTF-8''{quote(name)}"


@router.get("")
def export_novel(
    novel_id: int,
    format: str = "txt",
    scope: str = "published",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    novel = get_owned_novel(novel_id, user, db)
    chapters = _chapters(db, novel_id, scope)
    if not chapters:
        raise HTTPException(400, "没有可导出的章节")

    if format == "txt":
        parts = [f"《{novel.title}》", "", novel.summary or "", ""]
        for c in chapters:
            content = next((v.content for v in c.versions if v.id == c.current_version_id), "")
            parts.append(f"\n\n{'=' * 24}\n{c.title}\n{'=' * 24}\n\n{content}")
        return Response(
            content="\n".join(parts).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": _filename_header(f"{novel.title}.txt")},
        )

    if format == "epub":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            )
            manifest_items, spine_items, files = [], [], {}
            for i, c in enumerate(chapters):
                content = next((v.content for v in c.versions if v.id == c.current_version_id), "")
                body = "".join(f"<p>{escape(line)}</p>" for line in content.splitlines() if line.strip())
                files[f"chapter_{i}.xhtml"] = (
                    '<?xml version="1.0" encoding="utf-8"?>'
                    '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
                    f'<meta charset="utf-8"/><title>{escape(c.title)}</title></head>'
                    f"<body><h2>{escape(c.title)}</h2>{body}</body></html>"
                )
                manifest_items.append(
                    f'<item id="ch{i}" href="chapter_{i}.xhtml" media-type="application/xhtml+xml"/>'
                )
                spine_items.append(f'<itemref idref="ch{i}"/>')
            opf = (                '<?xml version="1.0" encoding="utf-8"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
                f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f'<dc:identifier id="uid">urn:xiaohuo:novel:{novel.id}</dc:identifier>'
                f"<dc:title>{escape(novel.title)}</dc:title>"
                f"<dc:language>zh-CN</dc:language>"
                '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>'
                f"<manifest><item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>"
                + "".join(manifest_items)
                + "</manifest>"
                f"<spine>{''.join(spine_items)}</spine></package>"
            )
            zf.writestr("OEBPS/content.opf", opf)
            nav_li = "".join(
                f"<li><a href='chapter_{i}.xhtml'>{escape(c.title)}</a></li>" for i, c in enumerate(chapters)
            )
            zf.writestr(
                "OEBPS/nav.xhtml",
                '<?xml version="1.0" encoding="utf-8"?>'
                '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                '<head><meta charset="utf-8"/><title>目录</title></head>'
                f"<body><nav epub:type='toc'><h1>目录</h1><ol>{nav_li}</ol></nav></body></html>",
            )
            for name, content in files.items():
                zf.writestr(f"OEBPS/{name}", content)
        return Response(
            content=buf.getvalue(),
            media_type="application/epub+zip",
            headers={"Content-Disposition": _filename_header(f"{novel.title}.epub")},
        )

    raise HTTPException(400, "format 仅支持 txt / epub")
