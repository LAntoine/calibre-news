"""Pure-Python EPUB 2.0.1 writer: OPF + NCX generation and zip packaging.

No native dependencies (no calibre C extensions, no lxml): the article tree
produced by the recipe engine is already XHTML, so packaging is just a zip
with mimetype first (stored, uncompressed) plus META-INF/container.xml.
"""

from __future__ import annotations

import os
import uuid
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_NS = "http://www.w3.org/1999/xhtml"

CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_MIME = {
    ".xhtml": "application/xhtml+xml",
    ".html": "text/html",
    ".css": "text/css",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ncx": "application/x-dtbncx+xml",
    ".opf": "application/oebps-package+xml",
}


def _mime_for(path: str) -> str:
    return _MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def _walk_files(root: str) -> list[str]:
    out = []
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name in ("content.opf", "toc.ncx"):
                continue
            out.append(os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/"))
    return sorted(out)


def _spine_items(feeds) -> list[tuple[str, str]]:
    """(idref, href) for index, feed indexes and articles, in reading order."""
    items = [("index", "index.xhtml")]
    for f, feed in enumerate(feeds):
        items.append(("feed_%d" % f, "feed_%d/index.xhtml" % f))
        for a, article in enumerate(feed):
            if getattr(article, "downloaded", False):
                items.append(("article_%d_%d" % (f, a), "feed_%d/article_%d/index.xhtml" % (f, a)))
    return items


def _manifest_files(root: str, cover_href: str | None) -> list[str]:
    files = _walk_files(root)
    if cover_href and cover_href not in files:
        files.append(cover_href)
    return files


def write_opf_and_ncx(recipe, feeds, dir: str) -> tuple[str, str]:
    title = recipe.short_title()
    pdate = recipe.publication_date()
    if getattr(recipe.output_profile, "periodical_date_in_title", False):
        title += pdate.strftime(recipe.timefmt)
    language = recipe.language if recipe.language and recipe.language != "und" else "en"
    author = recipe.__author__ or __appname__fallback()
    publisher = recipe.publisher or __appname__fallback()
    uid = "urn:uuid:%s" % uuid.uuid4()

    article_titles = []
    aseen = set()
    for _f, a in getattr(recipe, "aborted_articles", []):
        aseen.add(a.title)
    for _f, a, _tb in getattr(recipe, "failed_downloads", []):
        aseen.add(a.title)
    for f in feeds:
        for a in f:
            if a.title and a.title not in aseen:
                aseen.add(a.title)
                article_titles.append(a.title)
    desc = (_("Articles in this issue:") + "\n\n" + "\n\n".join(article_titles) + "\n\n" + str(recipe.description or "")).strip()

    cover_path = getattr(recipe, "cover_path", None)
    cover_href = None
    if cover_path and os.path.isfile(cover_path):
        cover_href = os.path.basename(cover_path)
    elif os.path.isfile(os.path.join(dir, "cover.png")):
        cover_href = "cover.png"

    manifest_files = _manifest_files(dir, cover_href)

    ids = {}
    for href in manifest_files:
        if href == cover_href:
            ids[href] = "cover-image"
        else:
            ids[href] = "f_" + re_id(href)
    ids["toc.ncx"] = "ncx"
    spine = [(i, h) for i, h in _spine_items(feeds) if h in ids]

    meta = [
        "<dc:title>%s</dc:title>" % escape(title),
        "<dc:creator>%s</dc:creator>" % escape(author),
        "<dc:language>%s</dc:language>" % escape(language),
        "<dc:identifier id=\"uid\">%s</dc:identifier>" % escape(uid),
        "<dc:date>%s</dc:date>" % pdate.strftime("%Y-%m-%dT%H:%M:%SZ"),
    ]
    if publisher:
        meta.append("<dc:publisher>%s</dc:publisher>" % escape(publisher))
    if desc:
        meta.append("<dc:description>%s</dc:description>" % escape(desc))
    if recipe.publication_type:
        meta.append('<meta name="publication_type" content="%s"/>' % escape(str(recipe.publication_type)))
    if cover_href:
        meta.append('<meta name="cover" content="cover-image"/>')

    manifest = ['<item id="%s" href=%s media-type="%s"/>' % (ids[h], quoteattr(h), _mime_for(h)) for h in manifest_files]
    manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    spine_xml = ['<itemref idref="%s"/>' % ids[href] for _idref, href in spine]

    guide = ['<reference type="toc" title="Table of Contents" href="index.xhtml"/>']
    if cover_href:
        guide.append('<reference type="cover" title="Cover" href=%s/>' % quoteattr(cover_href))

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="%s" version="2.0" unique-identifier="uid">\n'
        "<metadata xmlns:dc=\"%s\">\n%s\n</metadata>\n"
        "<manifest>\n%s\n</manifest>\n"
        '<spine toc="ncx">\n%s\n</spine>\n'
        "<guide>\n%s\n</guide>\n"
        "</package>\n"
        % (
            OPF_NS,
            DC_NS,
            "\n".join(meta),
            "\n".join(manifest),
            "\n".join(spine_xml),
            "\n".join(guide),
        )
    )
    opf_path = os.path.join(dir, "content.opf")
    with open(opf_path, "w", encoding="utf-8") as f:
        f.write(opf)

    ncx = _write_ncx(recipe, feeds, title, pdate, uid, dir)
    return opf_path, ncx


def _write_ncx(recipe, feeds, title: str, pdate: datetime, uid: str, dir: str) -> str:
    play_order = [0]

    def navpoint(idref, label, href, children_xml="") -> str:
        play_order[0] += 1
        return (
            '<navPoint id="%s" playOrder="%d"><navLabel><text>%s</text></navLabel>'
            "<content src=%s/>%s</navPoint>"
            % (idref, play_order[0], escape(label), quoteattr(href), children_xml)
        )

    top_children = ['<navPoint id="index" playOrder="1"><navLabel><text>%s</text></navLabel><content src="index.xhtml"/></navPoint>' % escape(title)]
    for f, feed in enumerate(feeds):
        art_xml = ""
        for a, article in enumerate(feed):
            if getattr(article, "downloaded", False):
                art_xml += navpoint("article_%d_%d" % (f, a), article.title, "feed_%d/article_%d/index.xhtml" % (f, a))
        top_children.append(navpoint("feed_%d" % f, feed.title, "feed_%d/index.xhtml" % f, art_xml))
    play_order[0] = 1
    body = "".join(top_children)
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="%s" version="2005-1">\n'
        "<head><meta name=\"dtb:uid\" content=%s/><meta name=\"dtb:depth\" content=\"3\"/>"
        "<meta name=\"dtb:totalPageCount\" content=\"0\"/><meta name=\"dtb:maxPageNumber\" content=\"0\"/></head>\n"
        "<docTitle><text>%s</text></docTitle>\n"
        "<navMap>\n%s\n</navMap>\n</ncx>\n"
        % (NCX_NS, quoteattr(uid), escape(title), body)
    )
    ncx_path = os.path.join(dir, "toc.ncx")
    with open(ncx_path, "w", encoding="utf-8") as f:
        f.write(ncx)
    return ncx_path


def re_id(name: str) -> str:
    import re as _re

    return _re.sub(r"[^A-Za-z0-9_\-]", "_", name) or "item"


def write_epub(src_dir: str, out_path: str) -> str:
    """Package the XHTML/OPF/NCX tree at src_dir into a valid EPUB 2.0.1 file."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML, compress_type=zipfile.ZIP_DEFLATED)
        for base, _dirs, files in os.walk(src_dir):
            for name in sorted(files):
                full = os.path.join(base, name)
                arc = "OEBPS/" + os.path.relpath(full, src_dir).replace(os.sep, "/")
                zf.write(full, arc, compress_type=zipfile.ZIP_DEFLATED)
    return out_path


def _(s):
    return s


def __appname__fallback():
    return "calibre-news"
