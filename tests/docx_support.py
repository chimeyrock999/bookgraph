"""Build tiny, valid DOCX files for parser tests without python-docx.

Each helper returns WordprocessingML snippets; :func:`write_docx` wraps a body into the minimal
OOXML package (content types, relationships, styles, and optional media) that mammoth reads.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

# A 1x1 transparent PNG: small, but a real image so decoders and MIME sniffing accept it.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_PACKAGE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="word/document.xml"/>
</Relationships>"""

_GRID_SPAN = re.compile(r'<w:gridSpan w:val="(\d+)"/>')

_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
</w:styles>"""


def paragraph(text: str, style: str | None = None) -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>"


def cell(content: str, *, grid_span: int = 1, v_merge: str | None = None) -> str:
    """One table cell. ``v_merge`` is ``"restart"`` on a rowspan's first cell, ``""`` below it."""

    props = ""
    if grid_span > 1:
        props += f'<w:gridSpan w:val="{grid_span}"/>'
    if v_merge == "restart":
        props += '<w:vMerge w:val="restart"/>'
    elif v_merge is not None:
        props += "<w:vMerge/>"
    body = content if content.startswith("<w:") else paragraph(content)
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{body}</w:tc>"


def table(rows: list[list[str]]) -> str:
    cols = max(_row_width(row) for row in rows)
    grid = "".join('<w:gridCol w:w="1000"/>' for _ in range(cols))
    body = "".join(f"<w:tr>{''.join(row)}</w:tr>" for row in rows)
    return f"<w:tbl><w:tblPr/><w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>"


def image_paragraph(rel_id: str = "rIdImg1", name: str = "Figure 1") -> str:
    """An inline picture referencing the media relationship ``rel_id``."""

    return (
        "<w:p><w:r><w:drawing>"
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<wp:extent cx="9525" cy="9525"/>'
        f'<wp:docPr id="1" name="{name}" descr="{name}"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="0" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/></pic:blipFill>'
        "<pic:spPr/></pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r></w:p>"
    )


def write_docx(path: Path, body: str, media: dict[str, bytes] | None = None) -> Path:
    """Write a DOCX whose ``word/document.xml`` body is ``body``.

    ``media`` maps a relationship id to PNG bytes, stored as ``word/media/<id>.png``.
    """

    media = media or {}
    rels = "".join(
        f'<Relationship Id="{rel_id}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="media/{rel_id}.png"/>'
        for rel_id in media
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _PACKAGE_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        for rel_id, data in media.items():
            archive.writestr(f"word/media/{rel_id}.png", data)
    return path


def _row_width(row: list[str]) -> int:
    spans = (_GRID_SPAN.search(item) for item in row)
    return sum(int(span.group(1)) if span else 1 for span in spans)
