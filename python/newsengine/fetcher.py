"""Article fetcher: download one article page, run the recipe hooks, localize images.

Port of calibre.web.fetch.simple.RecursiveFetcher, trimmed to the single-page
plus recursion pipeline that news recipes use.
"""

from __future__ import annotations

import os
import re
import threading
import time
import traceback
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .souputil import (
    decode_html,
    find,
    find_all,
    image_extension,
    is_wanted_link,
    normalize_tag_spec,
    save_soup,
)


class AbortArticle(Exception):
    pass


class FetchError(Exception):
    pass


def canonicalize_url(url: str) -> str:
    parts = list(urlsplit(url))
    parts[4] = ""
    return urlunsplit(parts)


def basename(url: str) -> str:
    path = urlparse(url).path
    name = path.rstrip("/").rsplit("/", 1)[-1] or "index"
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", name)


class ArticleFetcher:
    def __init__(self, recipe, log, image_map=None) -> None:
        self.recipe = recipe
        self.log = log
        self.image_map = image_map if image_map is not None else {}
        self._lock = threading.RLock()
        self.failed_links: list[tuple[str, str]] = []
        self.downloaded_paths: list[str] = []
        self.filemap: dict[str, str] = {}
        self.last_fetch_at = 0.0

    # --- low level fetching -------------------------------------------------

    def fetch_url(self, br, url: str, preloaded: bytes | None = None) -> tuple[bytes, str]:
        if preloaded is not None:
            return preloaded, url
        if url.startswith("data:"):
            from base64 import standard_b64decode

            return standard_b64decode(url.partition(",")[2]), url
        if url.startswith("file://") or url.startswith("file:"):
            path = url[7:] if url.startswith("file://") else url[5:]
            with open(path, "rb") as f:
                return f.read(), "file://" + path
        delta = time.monotonic() - self.last_fetch_at
        delay = self.recipe.get_url_specific_delay(url)
        if delay > 0 and delta < delay:
            time.sleep(delay - delta)
        url = canonicalize_url(url)
        open_func = getattr(br, "open_novisit", br.open)
        try:
            with open_func(url, timeout=self.recipe.timeout) as f:
                data = f.read()
                final_url = f.geturl()
                headers = f.info()
        except Exception:
            time.sleep(1)
            with open_func(url, timeout=self.recipe.timeout) as f:
                data = f.read()
                final_url = f.geturl()
                headers = f.info()
        finally:
            self.last_fetch_at = time.monotonic()
        self._last_headers = headers
        return data, final_url

    # --- HTML pipeline (mirrors RecursiveFetcher.get_soup) -------------------

    def get_soup(self, src: bytes, url: str | None) -> BeautifulSoup:
        content_type = None
        headers = getattr(self, "_last_headers", None)
        if headers is not None:
            content_type = headers.get("Content-Type")
        recipe = self.recipe
        usrc = decode_html(src, content_type)
        usrc = recipe.preprocess_raw_html(usrc, url)
        nmassage = list(recipe.preprocess_regexps)
        nmassage.append((re.compile(r"<!--.*?-->", re.DOTALL), lambda m: ""))
        for pat, repl in nmassage:
            usrc = pat.sub(repl, usrc)
        soup = BeautifulSoup(usrc, "html.parser")

        replace = recipe.skip_ad_pages(soup)
        if replace is not None:
            replace = decode_html(replace if isinstance(replace, bytes) else replace.encode("utf-8"))
            for pat, repl in nmassage:
                replace = pat.sub(repl, replace)
            soup = BeautifulSoup(replace, "html.parser")

        if recipe.keep_only_tags:
            body = soup.new_tag("body")
            specs = recipe.keep_only_tags
            if isinstance(specs, dict):
                specs = [specs]
            try:
                for spec in specs:
                    for tag in find_all(soup.find("body"), normalize_tag_spec(spec)):
                        body.insert(len(body.contents), tag)
                soup.find("body").replace_with(body)
            except AttributeError:
                pass

        def remove_beyond(tag, next_attr: str) -> None:
            while tag is not None and getattr(tag, "name", None) != "body":
                after = getattr(tag, next_attr)
                while after is not None:
                    ns = getattr(tag, next_attr)
                    after.extract()
                    after = ns
                tag = tag.parent

        if recipe.remove_tags_after is not None:
            rt = [recipe.remove_tags_after] if isinstance(recipe.remove_tags_after, dict) else recipe.remove_tags_after
            for spec in rt:
                remove_beyond(find(soup, normalize_tag_spec(spec)), "nextSibling")
        if recipe.remove_tags_before is not None:
            rt = [recipe.remove_tags_before] if isinstance(recipe.remove_tags_before, dict) else recipe.remove_tags_before
            for spec in rt:
                remove_beyond(find(soup, normalize_tag_spec(spec)), "previousSibling")

        for kwds in recipe.remove_tags:
            for tag in find_all(soup, normalize_tag_spec(kwds)):
                tag.extract()
        return recipe.preprocess_html(soup)

    # --- images / stylesheets / links ----------------------------------------

    def process_images(self, soup: BeautifulSoup, baseurl: str, article_dir: str, br) -> None:
        diskpath = os.path.join(article_dir, "images")
        os.makedirs(diskpath, exist_ok=True)
        c = 0
        for tag in soup.find_all("img", src=True):
            iurl = tag["src"]
            if iurl.startswith("data:"):
                try:
                    from base64 import standard_b64decode

                    data = standard_b64decode(iurl.partition(",")[2])
                except Exception:
                    self.log.exception("Failed to decode embedded image")
                    continue
            else:
                if callable(self.recipe.image_url_processor):
                    iurl = self.recipe.image_url_processor(baseurl, iurl)
                    if not iurl:
                        continue
                if not urlsplit(iurl).scheme:
                    iurl = urljoin(baseurl, iurl)
                with self._lock:
                    if iurl in self.image_map:
                        tag["src"] = self.image_map[iurl]
                        continue
                try:
                    data, _ = self.fetch_url(br, iurl)
                except Exception:
                    self.log.exception("Could not fetch image " + iurl)
                    continue
            c += 1
            fname = "img%d" % c
            if callable(self.recipe.preprocess_image):
                data = self.recipe.preprocess_image(data, iurl)
                if data is None:
                    continue
            ext = image_extension(data)
            imgpath = "images/%s.%s" % (fname, ext)
            with self._lock:
                self.image_map[iurl] = imgpath
            with open(os.path.join(article_dir, imgpath), "wb") as x:
                x.write(data)
            tag["src"] = imgpath

    def process_links(self, soup: BeautifulSoup, baseurl: str, article_dir: str, br, f: int, a: int, feed_len: int, recursion_level: int = 0) -> None:
        """Follow wanted links up to recipe.recursions levels; localize already-downloaded links."""
        diskpath = os.path.join(article_dir, "links")
        os.makedirs(diskpath, exist_ok=True)
        for c, tag in enumerate(list(soup.find_all("a", href=True))):
            iurl = tag["href"]
            if not urlsplit(iurl).scheme:
                iurl = urljoin(baseurl, iurl)
            nurl = canonicalize_url(iurl)
            if nurl in self.filemap:
                tag["href"] = self.filemap[nurl] + (("#" + urlsplit(iurl).fragment) if urlsplit(iurl).fragment else "")
                continue
            if recursion_level >= self.recipe.recursions:
                continue
            if not is_wanted_link(iurl, self.recipe._match_res, self.recipe._filter_res):
                continue
            try:
                is_wanted = self.recipe.is_link_wanted(iurl, tag)
            except NotImplementedError:
                is_wanted = True
            except Exception:
                is_wanted = False
            if not is_wanted:
                continue
            linkdir = "link%d" % c
            linkdiskpath = os.path.join(diskpath, linkdir)
            os.makedirs(linkdiskpath, exist_ok=True)
            try:
                data, newbaseurl = self.fetch_url(br, iurl)
                sub_soup = self.get_soup(data, iurl)
                base = sub_soup.find("base", href=True)
                if base is not None:
                    newbaseurl = base["href"]
                self.process_images(sub_soup, newbaseurl, linkdiskpath, br)
                _fname = basename(iurl)
                _fname = os.path.splitext(_fname)[0][:120] or "index"
                res = os.path.join(linkdiskpath, _fname + ".xhtml")
                self.downloaded_paths.append(res)
                self.filemap[nurl] = os.path.relpath(res, article_dir).replace(os.sep, "/")
                sub_soup = self.postprocess(sub_soup, newbaseurl, f, a, feed_len, first=False)
                save_soup(sub_soup, res)
                tag["href"] = self.filemap[nurl]
                if recursion_level + 1 < self.recipe.recursions:
                    self.process_links(sub_soup, newbaseurl, linkdiskpath, br, f, a, feed_len, recursion_level + 1)
            except AbortArticle:
                raise
            except Exception:
                self.failed_links.append((iurl, traceback.format_exc()))
                self.log.exception("Could not fetch link " + iurl)

    # --- postprocess (mirrors BasicNewsRecipe._postprocess_html) -------------

    def postprocess(self, soup: BeautifulSoup, url: str, f: int, a: int, feed_len: int, first: bool) -> BeautifulSoup:
        recipe = self.recipe
        if recipe.no_stylesheets:
            for link in soup.find_all("link"):
                if (link.get("type") or "text/css").lower() == "text/css" and "stylesheet" in (link.get("rel") or ("stylesheet",)):
                    link.extract()
            for style in soup.find_all("style"):
                style.extract()
        head = soup.find("head") or soup.find("body") or soup.find(True)
        if head is not None and head.name != "head":
            head = soup.new_tag("head")
            (soup.find("html") or soup).insert(0, head)
        css = recipe.template_css + "\n\n" + (recipe.get_extra_css() or "")
        style = soup.new_tag("style", type="text/css", title="override_css")
        style.append(css)
        if head is not None:
            head.append(style)
        if first:
            body = soup.find("body")
            if body is not None:
                navbar = self._navbar(f, a, feed_len, url)
                elem = BeautifulSoup(navbar, "html.parser").find("div")
                if elem is not None:
                    body.insert(0, elem)
        if recipe.remove_javascript:
            for script in list(soup.find_all("script")):
                script.extract()
            for o in soup.find_all(onload=True):
                del o["onload"]
        for attr in recipe.remove_attributes:
            for x in soup.find_all(attrs={attr: True}):
                del x[attr]
        for bad_tag in list(
            soup.find_all(["base", "iframe", "canvas", "embed", "button", "command", "datalist", "video", "audio", "noscript", "link", "meta"])
        ):
            bad_tag.extract()
        for img in soup.find_all("img", srcset=True):
            del img["srcset"]

        ans = recipe.postprocess_html(soup, first)

        for x in ans.find_all(["article", "aside", "header", "footer", "nav", "figcaption", "figure", "section"]):
            cls = x.get("class")
            if cls:
                if isinstance(cls, list):
                    cls.append("calibre-nuked-tag-%s" % x.name)
                else:
                    x["class"] = "%s calibre-nuked-tag-%s" % (cls, x.name)
            else:
                x["class"] = "calibre-nuked-tag-%s" % x.name
            x.name = "div"

        article = None
        try:
            article = recipe.feed_objects[f].articles[a]
        except Exception:
            pass
        if article is not None:
            recipe.populate_article_metadata(article, ans, first)
        return ans

    def _navbar(self, f: int, a: int, feed_len: int, url: str) -> str:
        recipe = self.recipe
        has_single_feed = getattr(recipe, "has_single_feed", False)
        parts = ['<div class="calibre_navbar" style="text-align:%s">' % ("center" if recipe.center_navbar else "left")]
        parts.append('<a href="../../index.xhtml">%s</a>' % _esc(recipe.short_title()))
        if not has_single_feed:
            parts.append(' &middot; <a href="../index.xhtml">%s</a>' % _esc("Section %d" % (f + 1)))
        if a > 0:
            parts.append(' &middot; <a href="../article_%d/index.xhtml">&lt;&lt;</a>' % (a - 1))
        if a < feed_len - 1:
            parts.append(' &middot; <a href="../article_%d/index.xhtml">&gt;&gt;</a>' % (a + 1))
        parts.append("</div>")
        return "".join(parts)

    # --- top level ------------------------------------------------------------

    def fetch_article(self, url: str, article_dir: str, f: int, a: int, feed_len: int, preloaded: bytes | None = None) -> str:
        recipe = self.recipe
        os.makedirs(article_dir, exist_ok=True)
        br = recipe.cloned_browser
        data, final_url = self.fetch_url(br, url, preloaded=preloaded)
        soup = self.get_soup(data, final_url)
        base = soup.find("base", href=True)
        if base is not None:
            final_url = base["href"]
        self.process_images(soup, final_url, article_dir, br)
        self.process_links(soup, final_url, article_dir, br, f, a, feed_len, recursion_level=0)
        soup = self.postprocess(soup, final_url, f, a, feed_len, first=True)
        res = os.path.join(article_dir, "index.xhtml")
        save_soup(soup, res)
        self.downloaded_paths.insert(0, res)
        return res


def _esc(s) -> str:
    from xml.sax.saxutils import escape

    return escape(str(s))
