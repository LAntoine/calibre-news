from __future__ import annotations

import os
import sys
import threading
import zipfile
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest


def posix_join(a: str, b: str) -> str:
    if not a:
        return b
    return a.rstrip("/") + "/" + b


def validate_epub(path: str) -> dict:
    """Structural validation of an EPUB 2.0.1 file. Returns info dict; raises on invalid."""
    assert os.path.isfile(path), "epub missing: %s" % path
    info = {"path": path, "size": os.path.getsize(path)}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        info["names"] = names
        assert names[0] == "mimetype", "mimetype must be the first entry"
        zi = zf.getinfo("mimetype")
        assert zi.compress_type == zipfile.ZIP_STORED, "mimetype must be stored uncompressed"
        assert zf.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in names
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        opf_path = container.find(".//c:rootfile", ns).get("full-path")
        assert opf_path in names, "opf %s not in zip" % opf_path
        opf_dir = os.path.dirname(opf_path)
        opf = ET.fromstring(zf.read(opf_path))
        info["opf_path"] = opf_path

        opf_ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
        meta = opf.find("opf:metadata", opf_ns)
        info["title"] = meta.find("dc:title", opf_ns).text
        info["language"] = meta.find("dc:language", opf_ns).text
        manifest = opf.find("opf:manifest", opf_ns)
        spine = opf.find("opf:spine", opf_ns)
        items = {i.get("id"): (i.get("href"), i.get("media-type")) for i in manifest.findall("opf:item", opf_ns)}
        info["manifest"] = sorted(items)
        for id_, (href, _mt) in items.items():
            full = posix_join(opf_dir, href)
            assert full in names, "manifest entry %s -> %s missing from zip" % (id_, full)
        for n in names:
            if n.startswith(opf_dir + "/") and not n.endswith((".opf", ".ncx")):
                rel = n[len(opf_dir) + 1 :]
                assert any(h == rel for h, _m in items.values()), "file %s not in manifest" % n
        spine_refs = [r.get("idref") for r in spine.findall("opf:itemref", opf_ns)]
        info["spine"] = spine_refs
        for r in spine_refs:
            assert r in items, "spine idref %s not in manifest" % r
        ncx_href = items[spine.get("toc")][0]
        ncx_full = posix_join(opf_dir, ncx_href)
        ncx = ET.fromstring(zf.read(ncx_full))
        ncx_ns = {"n": "http://www.daisy.org/z3986/2005/ncx/"}
        navpoints = ncx.findall(".//n:navPoint", ncx_ns)
        info["navpoints"] = len(navpoints)
        assert navpoints, "ncx has no navPoints"
        for np in navpoints:
            src = np.find("n:content", ncx_ns).get("src")
            full = posix_join(opf_dir, src)
            assert full in names, "ncx content %s missing from zip" % full
        cover_meta = meta.find("opf:meta[@name='cover']", opf_ns)
        if cover_meta is not None:
            cid = cover_meta.get("content")
            assert cid in items, "cover id %s not in manifest" % cid
            info["cover"] = items[cid][0]
        for n in names:
            if n.endswith((".xhtml", ".html")):
                ET.fromstring(zf.read(n))
        for n in names:
            if n.endswith(".xhtml"):
                root = ET.fromstring(zf.read(n))
                xhtml = {"x": "http://www.w3.org/1999/xhtml"}
                base = os.path.dirname(n)
                for img in root.findall(".//x:img", xhtml):
                    src = img.get("src")
                    if src and not src.startswith(("http://", "https://", "data:")):
                        assert posix_join(base, src) in names, "img %s in %s missing from zip" % (src, n)
    return info


@pytest.fixture
def epub_validator():
    return validate_epub


# --- local news server fixture -------------------------------------------------

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c626001000000ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)

ARTICLE_HTML = """<html><head><title>Article One</title><style>.x{color:red}</style></head>
<body><div class="ad">ADVERTISEMENT</div>
<h1 class="heading article__title">Article One</h1>
<div class="article__content"><p>First paragraph of article one.</p>
<img src="/img.png"/><p>Second paragraph.</p></div>
<script>evil()</script></body></html>"""

ARTICLE2_HTML = """<html><head><title>Article Two</title></head>
<body><h1 class="heading article__title">Article Two</h1>
<div class="article__content"><p>Body of article two.</p></div></body></html>"""

RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Test Paper</title><description>desc</description>
<item><title>Article One</title><link>{base}/article1.html</link>
<pubDate>{date}</pubDate><description>summary one</description></item>
<item><title>Article Two</title><link>{base}/article2.html</link>
<pubDate>{date}</pubDate><description>summary two</description></item>
<item><title>Article One</title><link>{base}/article1.html</link>
<pubDate>{date}</pubDate><description>duplicate of one</description></item>
</channel></rss>"""


class _Handler(BaseHTTPRequestHandler):
    base = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        from datetime import datetime, timezone

        date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        if self.path == "/feed.xml":
            body = RSS_XML.format(base=self.base, date=date).encode()
            ctype = "application/rss+xml"
        elif self.path == "/article1.html":
            body, ctype = ARTICLE_HTML.encode(), "text/html; charset=utf-8"
        elif self.path == "/article2.html":
            body, ctype = ARTICLE2_HTML.encode(), "text/html; charset=utf-8"
        elif self.path == "/img.png":
            body, ctype = PNG_1PX, "image/png"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="session")
def news_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.base = "http://127.0.0.1:%d" % server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield _Handler.base
    server.shutdown()


@pytest.fixture
def test_recipe_file(tmp_path, news_server):
    recipe = """from calibre.web.feeds.news import BasicNewsRecipe

class TestPaper(BasicNewsRecipe):
    title = 'Test Paper'
    __author__ = 'tests'
    description = 'A test newspaper'
    language = 'en'
    publication_type = 'newspaper'
    oldest_article = 7
    no_stylesheets = True
    ignore_duplicate_articles = {'title', 'url'}
    keep_only_tags = [
        dict(name='h1', attrs={'class': ['heading', 'article__title']}),
        dict(name='div', attrs={'class': ['article__content']}),
    ]
    remove_tags = [dict(name='div', attrs={'class': ['ad']})]
    feeds = [('News', '%s/feed.xml')]
""" % (
        news_server,
    )
    p = tmp_path / "test_paper.recipe"
    p.write_text(recipe, encoding="utf-8")
    return str(p)
