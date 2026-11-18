"""HTML decoding, recipe tag-spec normalization, image sniffing and XHTML serialization."""

from __future__ import annotations

import re
import struct
import zlib
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr

VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
RAW_TEXT_ELEMENTS = frozenset(["style", "script"])

_XML_ENCODING_RE = re.compile(br"""<\?xml[^>]*encoding=["']([^"']+)["']""", re.IGNORECASE)
_META_CHARSET_RE = re.compile(br"""<meta[^>]+charset=["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE)
_CT_CHARSET_RE = re.compile(r"charset=([a-zA-Z0-9_\-]+)", re.IGNORECASE)


def decode_html(data: bytes, content_type: str | None = None) -> str:
    """Decode raw bytes to text, mirroring calibre's xml_to_unicode heuristics."""
    if isinstance(data, str):
        return data
    def _g(m):
        g = m.group(1)
        return g.decode("ascii", "replace") if isinstance(g, bytes) else g

    enc = None
    if content_type:
        m = _CT_CHARSET_RE.search(content_type)
        if m:
            enc = _g(m)
    if not enc:
        m = _XML_ENCODING_RE.search(data[:4096])
        if m:
            enc = _g(m)
    if not enc:
        m = _META_CHARSET_RE.search(data[:4096])
        if m:
            enc = _g(m)
    if not enc:
        enc = "utf-8"
    try:
        return data.decode(enc, "replace")
    except LookupError:
        return data.decode("utf-8", "replace")


def _class_matcher(values) -> callable:
    q = frozenset(str(v) for v in values)

    def matcher(x) -> bool:
        return bool(x) and bool(frozenset(str(x).split()).intersection(q))

    return matcher


def normalize_tag_spec(spec: dict) -> dict:
    """Normalize a recipe tag spec (``dict(name=..., attrs={...})``) for bs4.

    calibre's BeautifulSoup treats list-valued ``class`` searches as "any of
    these classes"; bs4 only matches exact lists, so convert them to callables.
    List values for other attributes mean "equals any of these".
    """
    out = dict(spec)
    attrs = out.get("attrs")
    if attrs:
        new_attrs = {}
        for k, v in attrs.items():
            if k == "class" and isinstance(v, (list, tuple)):
                new_attrs[k] = _class_matcher(v)
            elif isinstance(v, (list, tuple)) and not callable(v):
                values = frozenset(v)
                new_attrs[k] = lambda x, values=values: x in values
            else:
                new_attrs[k] = v
        out["attrs"] = new_attrs
    return out


def find_all(soup, spec):
    return soup.find_all(**normalize_tag_spec(spec))


def find(soup, spec):
    return soup.find(**normalize_tag_spec(spec))


def image_type(data: bytes) -> str | None:
    """Sniff an image format (imghdr replacement, which Python 3.13 removed)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if data[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis"):
            return "heic"
    head = data[:1024].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:1024]):
        return "svg"
    return None


def image_extension(data: bytes) -> str:
    t = image_type(data)
    if t == "svg":
        return "svg"
    if t in (None, "gif"):
        # keep gif as gif (readers support it); unknown formats are stored as png-ish bin
        return t or "img"
    return t


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Generate a solid-colour PNG without Pillow (used for the default cover)."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


_NAME_BAD_CHARS_RE = re.compile(r"[^A-Za-z0-9_.\-:]")
_NAME_START_BAD_RE = re.compile(r"^[^A-Za-z_:]")


def _xml_name(name: str, fallback: str = "div") -> str:
    name = _NAME_BAD_CHARS_RE.sub("_", str(name))
    name = _NAME_START_BAD_RE.sub("a", name)
    return name or fallback


def _serialize_attrs(tag) -> str:
    parts = []
    for k, v in tag.attrs.items():
        k = _xml_name(k, fallback="attr")
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v)
        if v is None:
            v = k
        parts.append("%s=%s" % (k, quoteattr(str(v))))
    return (" " + " ".join(parts)) if parts else ""


def to_xhtml(node) -> str:
    """Serialize a bs4 node to well-formed XHTML (XML) text."""
    from bs4 import CData, Comment, Doctype, NavigableString, ProcessingInstruction, Tag

    if node is None:
        return ""
    if isinstance(node, Tag):
        name = _xml_name(node.name)
        attrs = _serialize_attrs(node)
        if name in VOID_ELEMENTS:
            return "<%s%s/>" % (name, attrs)
        inner = "".join(to_xhtml(c) for c in node.contents)
        if name in RAW_TEXT_ELEMENTS:
            inner = "".join(str(c) for c in node.contents)
        return "<%s%s>%s</%s>" % (name, attrs, inner, name)
    if isinstance(node, (CData,)):
        return "<![CDATA[%s]]>" % node
    if isinstance(node, Comment):
        return "<!--%s-->" % node
    if isinstance(node, Doctype):
        return ""
    if isinstance(node, ProcessingInstruction):
        return str(node)
    if isinstance(node, NavigableString):
        return escape(str(node))
    return escape(str(node))


XHTML_DOCTYPE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
)


def render_xhtml_document(soup, lang: str | None = None) -> str:
    """Render a whole parsed HTML document as an XHTML 1.1 string."""
    html_tag = soup.find("html")
    if html_tag is not None:
        html_tag["xmlns"] = "http://www.w3.org/1999/xhtml"
        if lang:
            html_tag["xml:lang"] = lang
            html_tag["lang"] = lang
        head = html_tag.find("head")
        if head is not None:
            for meta in head.find_all("meta"):
                if meta.get("charset") is not None or "charset" in (meta.get("content") or "").lower():
                    meta.extract()
            head.insert(0, soup.new_tag("meta", charset="utf-8"))
        return XHTML_DOCTYPE + to_xhtml(html_tag)
    body = soup.find("body") or soup
    return (
        XHTML_DOCTYPE
        + '<html xmlns="http://www.w3.org/1999/xhtml"%s><head><meta charset="utf-8"/></head><body>%s</body></html>'
        % (' xml:lang="%s" lang="%s"' % (lang, lang) if lang else "", to_xhtml(body))
    )


def save_soup(soup, target: str, base_dir: str | None = None) -> None:
    """Save a soup as an XHTML file, rewriting absolute local image/link paths to relative."""
    import os

    selfdir = os.path.dirname(target)
    for tag in soup.find_all(["img", "link", "a"]):
        for key in ("src", "href"):
            path = tag.get(key, None)
            if path and os.path.isabs(path) and os.path.isfile(path):
                rel = os.path.relpath(path, selfdir).replace(os.sep, "/")
                tag[key] = rel
    with open(target, "wb") as f:
        f.write(render_xhtml_document(soup).encode("utf-8"))


def is_wanted_link(url: str, match_regexps, filter_regexps) -> bool:
    if urlsplit(url).scheme not in ("http", "https", ""):
        return False
    for pat in (r"\.exe\s*$", r"\.mp3\s*$", r"\.ogg\s*$", r"^\s*mailto:", r"^\s*$"):
        if re.search(pat, url, re.IGNORECASE):
            return False
    if filter_regexps:
        for f in filter_regexps:
            if f.search(url):
                return False
    if match_regexps:
        return any(m.search(url) for m in match_regexps)
    return True
