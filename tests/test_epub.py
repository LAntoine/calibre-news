from __future__ import annotations

import os
import time
import zipfile
import xml.etree.ElementTree as ET

from newsengine.epub import write_epub, write_opf_and_ncx
from newsengine.feeds import Article, Feed
from newsengine.recipe import BasicNewsRecipe, RecipeOptions


def _make_tree(d):
    os.makedirs(os.path.join(d, "feed_0", "article_0", "images"), exist_ok=True)
    with open(os.path.join(d, "index.xhtml"), "w") as f:
        f.write('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body><h1>T</h1><a href="feed_0/index.xhtml">sec</a></body></html>')
    with open(os.path.join(d, "feed_0", "index.xhtml"), "w") as f:
        f.write('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>S</title></head><body><a href="article_0/index.xhtml">a</a></body></html>')
    with open(os.path.join(d, "feed_0", "article_0", "index.xhtml"), "w") as f:
        f.write('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>A</title></head><body><p>hi</p><img src="images/img1.png"/></body></html>')
    with open(os.path.join(d, "feed_0", "article_0", "images", "img1.png"), "wb") as f:
        f.write(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63600100000005000157bfabd40000000049454e44ae426082"))
    with open(os.path.join(d, "cover.png"), "wb") as f:
        from newsengine.souputil import solid_png

        f.write(solid_png(10, 10, (1, 2, 3)))


def _recipe():
    class R(BasicNewsRecipe):
        title = "Unit Paper"
        language = "en"

    return R(RecipeOptions())


def test_opf_ncx_and_epub_package(tmp_path, epub_validator):
    d = str(tmp_path / "tree")
    os.makedirs(d)
    _make_tree(d)
    r = _recipe()
    feed = Feed()
    feed.title = "Section"
    a = Article("1", "Art", "http://x", None, None, time.gmtime())
    a.downloaded = True
    feed.articles.append(a)
    r.cover_path = os.path.join(d, "cover.png")
    write_opf_and_ncx(r, [feed], d)
    assert os.path.isfile(os.path.join(d, "content.opf"))
    assert os.path.isfile(os.path.join(d, "toc.ncx"))

    out = str(tmp_path / "out.epub")
    write_epub(d, out)
    info = epub_validator(out)
    assert info["title"] == "Unit Paper"
    assert info["language"] == "en"
    assert "OEBPS/cover.png" in info["names"]
    assert info.get("cover") == "cover.png"
    assert info["navpoints"] >= 3  # index + feed + article
    assert any("article_0" in s for s in info["spine"])


def test_solid_png_is_valid_png(tmp_path):
    from newsengine.souputil import solid_png

    data = solid_png(4, 4, (255, 0, 0))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    import struct
    import zlib

    # parse chunks
    pos = 8
    chunks = []
    while pos < len(data):
        ln = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + ln]
        crc = struct.unpack(">I", data[pos + 8 + ln : pos + 12 + ln])[0]
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF
        chunks.append(tag)
        pos += 12 + ln
    assert chunks == [b"IHDR", b"IDAT", b"IEND"]


def test_mimetype_first_and_stored(tmp_path):
    d = str(tmp_path / "t")
    os.makedirs(d)
    with open(os.path.join(d, "index.xhtml"), "w") as f:
        f.write('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body/></html>')
    r = _recipe()
    feed = Feed()
    feed.title = "S"
    write_opf_and_ncx(r, [feed], d)
    out = str(tmp_path / "a.epub")
    write_epub(d, out)
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist()[0] == "mimetype"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
