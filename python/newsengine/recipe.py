"""BasicNewsRecipe: a portable, API-compatible port of calibre's news recipe base class.

Recipes written for calibre (``from calibre.web.feeds.news import BasicNewsRecipe``)
run unmodified against this class via the shim in newsengine.recipes.
The download pipeline mirrors calibre.web.feeds.news.BasicNewsRecipe:
parse feeds -> deduplicate -> download cover -> fetch articles (with recipe
hooks) -> write index pages + OPF/NCX -> package EPUB.
"""

from __future__ import annotations

import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

from .browser import DEFAULT_USER_AGENT, Browser
from .feeds import Article, Feed, feed_from_xml, feeds_from_index
from .fetcher import AbortArticle, ArticleFetcher
from .log import Log, ThreadSafeWrapper, default_log
from .souputil import decode_html, image_extension

__appname__ = "calibre-news"


def _(s):
    return s


def ngettext(singular, plural, n):
    return singular if n == 1 else plural


def classes(classes_str: str):
    q = frozenset(classes_str.split(" "))
    return dict(attrs={"class": lambda x: x and frozenset(x.split()).intersection(q)})


def prefixed_classes(classes_str: str):
    q = frozenset(classes_str.split(" "))

    def matcher(x) -> bool:
        if x:
            for candidate in frozenset(x.split()):
                for c in q:
                    if candidate.startswith(c):
                        return True
        return False

    return dict(attrs={"class": matcher})


class RecipeOptions:
    """Stand-in for calibre's parsed command line options."""

    def __init__(self, **kw) -> None:
        self.verbose = kw.get("verbose", 0)
        self.test = kw.get("test", None)
        self.username = kw.get("username", None)
        self.password = kw.get("password", None)
        self.lrf = kw.get("lrf", False)
        self.output_profile = kw.get("output_profile", None) or _DefaultOutputProfile()
        self.recipe_specific_option = kw.get("recipe_specific_option", [])


class _DefaultOutputProfile:
    short_name = "default"
    touchscreen = False
    screen_size = None
    periodical_date_in_title = False
    touchscreen_news_css = ""


class BasicNewsRecipe:
    """Base class for news recipes. Port of calibre's BasicNewsRecipe."""

    title = _("Unknown News Source")
    description = ""
    __author__ = __appname__
    requires_version = (0, 6, 0)
    language = "und"
    publisher = None
    max_articles_per_feed: int = 100
    oldest_article = 7.0
    recursions: int = 0
    delay = 0
    publication_type = "unknown"
    simultaneous_downloads: int = 5
    timeout = 120.0
    timefmt = " [%a, %d %b %Y]"
    feeds = None
    summary_length = 500
    no_stylesheets = False
    remove_javascript = True
    needs_subscription = False
    center_navbar = True
    encoding = None
    use_embedded_content = None
    articles_are_obfuscated = False
    reverse_article_order = False
    auto_cleanup = False
    auto_cleanup_keep = None
    extra_css = None
    remove_empty_feeds = False
    match_regexps = []
    filter_regexps = []
    conversion_options = {}
    remove_tags = []
    remove_tags_after = None
    remove_tags_before = None
    remove_attributes = []
    keep_only_tags = []
    preprocess_regexps = []
    template_css = """
            .article_date { color: gray; font-family: monospace; }
            .article_description { text-indent: 0pt; }
            a.article { font-weight: bold; text-align: left; }
            a.feed { font-weight: bold; }
            .calibre_navbar { font-family: monospace; }
    """
    masthead_url = None
    cover_url = None
    cover_margins = (0, 0, "#ffffff")
    recipe_disabled = None
    ignore_duplicate_articles = None
    compress_news_images = False
    compress_news_images_auto_size = 16
    compress_news_images_max_size = None
    scale_news_images_to_device = True
    scale_news_images = None
    resolve_internal_links = False
    recipe_specific_options = None
    browser_type = "mechanize"
    handle_gzip = True
    calibre_most_common_ua: str = ""
    browser_user_agent = None

    # --- hooks recipes override ---------------------------------------------

    def short_title(self) -> str:
        return str(self.title)

    def is_link_wanted(self, url: str, tag) -> bool:
        raise NotImplementedError()

    def get_extra_css(self):
        return self.extra_css

    def get_cover_url(self):
        return getattr(self, "cover_url", None)

    def get_masthead_url(self):
        return getattr(self, "masthead_url", None)

    def get_feeds(self):
        if not self.feeds:
            raise NotImplementedError()
        if self.test:
            return self.feeds[: self.test[0]]
        return self.feeds

    def get_url_specific_delay(self, url: str) -> float:
        return self.delay

    @classmethod
    def print_version(cls, url: str):
        raise NotImplementedError()

    @classmethod
    def image_url_processor(cls, baseurl: str, url: str):
        return url

    def preprocess_image(self, img_data: bytes, image_url: str):
        return img_data

    def get_browser(self, *args, **kwargs) -> Browser:
        ua = kwargs.get("user_agent") or getattr(self, "last_used_user_agent", None) or self.browser_user_agent or self.calibre_most_common_ua or DEFAULT_USER_AGENT
        kwargs["user_agent"] = ua
        self.last_used_user_agent = ua
        br = Browser(*args, timeout=self.timeout, **kwargs)
        br.addheaders += [("Accept", "*/*")]
        if self.handle_gzip:
            br.set_handle_gzip(True)
        return br

    def clone_browser(self, br: Browser) -> Browser:
        if callable(getattr(br, "clone_browser", None)):
            return br.clone_browser()
        return self.get_browser()

    @property
    def cloned_browser(self) -> Browser:
        return self.clone_browser(self.browser)

    def get_article_url(self, article):
        for key in article.keys():
            if key.endswith("_origlink"):
                url = article[key]
                if url and str(url).startswith(("http://", "https://")):
                    return url
        ans = article.get("link", None)
        if not ans and getattr(article, "links", None):
            for item in article.links:
                if item.get("rel", "alternate") == "alternate":
                    ans = item["href"]
                    break
        return ans

    def skip_ad_pages(self, soup):
        return None

    def abort_article(self, msg=None):
        raise AbortArticle(msg or "")

    def preprocess_raw_html(self, raw_html: str, url: str) -> str:
        return raw_html

    def preprocess_html(self, soup):
        return soup

    def postprocess_html(self, soup, first_fetch: bool):
        return soup

    def cleanup(self) -> None:
        pass

    def parse_index(self):
        raise NotImplementedError()

    def sort_index_by(self, index, weights):
        return index

    def abort_recipe_processing(self, msg=None):
        raise AbortArticle(msg or "")

    def get_obfuscated_article(self, url: str):
        raise NotImplementedError()

    def populate_article_metadata(self, article: Article, soup, first: bool) -> None:
        pass

    def postprocess_book(self, oeb, opts, log) -> None:
        pass

    # --- engine --------------------------------------------------------------

    def __init__(self, options=None, log: Log = default_log, progress_reporter=None) -> None:
        self.log = ThreadSafeWrapper(log)
        if not isinstance(self.title, str):
            self.title = str(self.title)
        options = options or RecipeOptions()
        self.debug = options.verbose > 1
        self.verbose = options.verbose
        self.test = options.test
        if self.test and not isinstance(self.test, tuple):
            self.test = (2, 2)
        self.username = options.username
        self.password = options.password
        self.output_profile = options.output_profile
        self.touchscreen = getattr(self.output_profile, "touchscreen", False)
        if self.touchscreen:
            self.template_css += getattr(self.output_profile, "touchscreen_news_css", "")
        if self.test:
            self.max_articles_per_feed = self.test[1]
        if self.debug:
            self.verbose = True
        self.report_progress = progress_reporter or (lambda *a, **k: None)
        if self.needs_subscription and not self.username and self.needs_subscription != "optional":
            raise ValueError(_('The "%s" recipe needs a username and password.') % self.title)
        self.output_dir = os.path.abspath(os.getcwd())
        self.browser = self.get_browser()
        self.image_map: dict[str, str] = {}
        self.failed_downloads = []
        self.partial_failures = []
        self.aborted_articles = []
        self._match_res = [re.compile(i, re.IGNORECASE) for i in self.match_regexps]
        self._filter_res = [re.compile(i, re.IGNORECASE) for i in self.filter_regexps]
        if self.delay > 0:
            self.simultaneous_downloads = 1
        rso = self.recipe_specific_options or {}
        self.recipe_specific_options_metadata = rso
        self.recipe_specific_options = {k: rso[k]["default"] for k in rso if "default" in rso}
        for x in options.recipe_specific_option or ():
            k, sep, v = x.partition(":")
            if k in rso:
                self.recipe_specific_options[k] = v

    def publication_date(self) -> datetime:
        return datetime.now(timezone.utc).astimezone()

    @property
    def lang_for_html(self):
        return self.language if self.language != "und" else None

    # --- feed parsing ---------------------------------------------------------

    def parse_feeds(self) -> list:
        feeds = self.get_feeds()
        parsed_feeds = []
        br = self.browser
        for obj in feeds:
            if isinstance(obj, (str, bytes)):
                title, url = None, obj
            else:
                title, url = obj
            if isinstance(title, bytes):
                title = title.decode("utf-8")
            if isinstance(url, bytes):
                url = url.decode("utf-8")
            if url.startswith("feed://"):
                url = "http" + url[4:]
            self.report_progress(0, _("Fetching feed") + " %s..." % (title or url))
            try:
                purl = urlparse(url, allow_fragments=False)
                if purl.username or purl.password:
                    hostname = purl.hostname or ""
                    if purl.port:
                        hostname += ":%d" % purl.port
                    url = purl._replace(netloc=hostname).geturl()
                with br.open_novisit(url, timeout=self.timeout) as f:
                    raw = f.read()
                parsed_feeds.append(
                    feed_from_xml(
                        raw,
                        title=title,
                        log=self.log,
                        oldest_article=self.oldest_article,
                        max_articles_per_feed=self.max_articles_per_feed,
                        get_article_url=self.get_article_url,
                    )
                )
            except Exception as err:
                feed = Feed(log=self.log)
                msg = "Failed feed: %s" % (title or url)
                feed.populate_from_preparsed_feed(msg, [])
                feed.description = str(err)
                parsed_feeds.append(feed)
                self.log.exception(msg)
            delay = self.get_url_specific_delay(url)
            if delay > 0:
                time.sleep(delay)
        if self.remove_empty_feeds:
            parsed_feeds = [f for f in parsed_feeds if len(f) > 0]
        return parsed_feeds

    def remove_duplicate_articles(self, feeds: list) -> list:
        from collections import defaultdict

        seen_keys = defaultdict(set)
        remove = []
        for f in feeds:
            for article in f:
                for key in self.ignore_duplicate_articles or ():
                    val = getattr(article, key)
                    seen = seen_keys[key]
                    if val:
                        if val in seen:
                            remove.append((f, article))
                        else:
                            seen.add(val)
        for feed, article in remove:
            self.log.debug("Removing duplicate article: %s from section: %s" % (article.title, feed.title))
            feed.remove_article(article)
        if self.remove_empty_feeds:
            feeds = [f for f in feeds if len(f) > 0]
        return feeds

    # --- cover / masthead ------------------------------------------------------

    def download_cover(self) -> None:
        self.cover_path = None
        cu = self.get_cover_url()
        if not cu:
            return
        try:
            if hasattr(cu, "read"):
                data = cu.read()
            else:
                with self.browser.open_novisit(cu, timeout=self.timeout) as f:
                    data = f.read()
            from .souputil import image_type

            if image_type(data) is None:
                self.log.warning("Cover URL %s did not return an image, skipping" % cu)
                return
            ext = image_extension(data) or "jpg"
            if ext == "jpeg":
                ext = "jpg"
            path = os.path.join(self.output_dir, "cover." + ext)
            with open(path, "wb") as pf:
                pf.write(data)
            self.cover_path = path
        except Exception:
            self.log.exception("Failed to download cover from %s" % cu)

    def resolve_masthead(self) -> None:
        self.masthead_path = None
        mu = self.get_masthead_url()
        if not mu:
            return
        try:
            if hasattr(mu, "read"):
                data = mu.read()
            else:
                with self.browser.open_novisit(mu, timeout=self.timeout) as f:
                    data = f.read()
            ext = image_extension(data) or "jpg"
            path = os.path.join(self.output_dir, "masthead." + ext)
            with open(path, "wb") as pf:
                pf.write(data)
            self.masthead_path = path
        except Exception:
            self.log.exception("Failed to download masthead")

    def default_cover(self) -> str | None:
        from .souputil import solid_png

        path = os.path.join(self.output_dir, "cover.png")
        with open(path, "wb") as pf:
            pf.write(solid_png(600, 800, (30, 30, 60)))
        return path

    # --- index pages -----------------------------------------------------------

    def _page(self, title: str, body_html: str) -> bytes:
        from .souputil import XHTML_DOCTYPE

        lang = self.lang_for_html
        lang_attr = ' xml:lang="%s" lang="%s"' % (lang, lang) if lang else ""
        css = self.template_css + "\n\n" + (self.get_extra_css() or "")
        return (
            XHTML_DOCTYPE
            + '<html xmlns="http://www.w3.org/1999/xhtml"%s><head><meta charset="utf-8"/>'
            '<title>%s</title><style type="text/css">%s</style></head><body>%s</body></html>'
            % (lang_attr, _esc(title), css, body_html)
        ).encode("utf-8")

    def feeds2index(self, feeds: list) -> bytes:
        from xml.sax.saxutils import escape

        pdate = self.publication_date()
        parts = [
            "<h1>%s</h1>" % escape(self.short_title() + pdate.strftime(self.timefmt)),
            '<p class="article_date">%s</p>' % escape(pdate.strftime("%a, %d %b %Y %H:%M")),
        ]
        if self.description:
            parts.append("<p>%s</p>" % escape(self.description))
        parts.append("<ul>")
        for f, feed in enumerate(feeds):
            parts.append(
                '<li><a class="feed" href="feed_%d/index.xhtml">%s</a> (%d)</li>'
                % (f, escape(feed.title), len(feed))
            )
        parts.append("</ul>")
        return self._page(self.short_title(), "".join(parts))

    def feed2index(self, f: int, feeds: list) -> bytes:
        from xml.sax.saxutils import escape

        feed = feeds[f]
        parts = ["<h1>%s</h1>" % escape(feed.title), '<p><a href="../index.xhtml">%s</a></p>' % escape(self.short_title())]
        parts.append("<ul>")
        for a, article in enumerate(feed):
            title = escape(article.title)
            link = 'href="article_%d/index.xhtml"' % a if getattr(article, "downloaded", False) else ""
            date = escape(article.formatted_date or "")
            author = escape(article.author) if article.author else ""
            summary = escape(article.text_summary or "")[: self.summary_length]
            parts.append(
                "<li><a class=\"article\" %s>%s</a>%s%s<p class=\"article_description\">%s</p></li>"
                % (link, title, (" " + author) if author else "", date, summary)
            )
        parts.append("</ul>")
        return self._page(feed.title, "".join(parts))

    # --- article fetching -------------------------------------------------------

    def fetch_article(self, url: str, article_dir: str, f: int, a: int, feed_len: int) -> str:
        fetcher = ArticleFetcher(self, self.log, self.image_map)
        return fetcher.fetch_article(url, article_dir, f, a, feed_len)

    def fetch_obfuscated_article(self, url: str, article_dir: str, f: int, a: int, feed_len: int) -> str:
        x = self.get_obfuscated_article(url)
        if isinstance(x, dict):
            data = x["data"]
            if isinstance(data, str):
                data = data.encode(self.encoding or "utf-8")
            url = x.get("url", url)
        else:
            with open(x, "rb") as of:
                data = of.read()
            os.remove(x)
        fetcher = ArticleFetcher(self, self.log, self.image_map)
        return fetcher.fetch_article(url, article_dir, f, a, feed_len, preloaded=data)

    def fetch_embedded_article(self, article: Article, article_dir: str, f: int, a: int, feed_len: int) -> str:
        from xml.sax.saxutils import escape

        body = "<h1>%s</h1>%s" % (escape(article.title), article.content or "")
        raw = self._page(article.title, body)
        fetcher = ArticleFetcher(self, self.log, self.image_map)
        return fetcher.fetch_article("file://" + _write_temp(raw), article_dir, f, a, feed_len)

    # --- main pipeline -----------------------------------------------------------

    def build_index(self) -> str:
        self.report_progress(0, _("Fetching feeds..."))
        feeds = None
        try:
            feeds = feeds_from_index(
                self.parse_index(),
                oldest_article=self.oldest_article,
                max_articles_per_feed=self.max_articles_per_feed,
                log=self.log,
            )
            self.report_progress(0, _("Got feeds from index page"))
        except NotImplementedError:
            pass
        if feeds is None:
            feeds = self.parse_feeds()
        if not feeds:
            raise ValueError("No articles found, aborting")
        if self.ignore_duplicate_articles is not None:
            feeds = self.remove_duplicate_articles(feeds)

        self.report_progress(0, _("Trying to download cover..."))
        self.download_cover()
        self.report_progress(0, _("Generating masthead..."))
        self.resolve_masthead()

        if self.test:
            feeds = feeds[: self.test[0]]
        self.has_single_feed = len(feeds) == 1
        self.feed_objects = feeds

        if self.reverse_article_order:
            for feed in feeds:
                feed.reverse()

        index = os.path.join(self.output_dir, "index.xhtml")
        with open(index, "wb") as fi:
            fi.write(self.feeds2index(feeds))

        jobs = []
        for f, feed in enumerate(feeds):
            feed_dir = os.path.join(self.output_dir, "feed_%d" % f)
            os.makedirs(feed_dir, exist_ok=True)
            for a, article in enumerate(feed):
                if a >= self.max_articles_per_feed:
                    break
                art_dir = os.path.join(feed_dir, "article_%d" % a)
                os.makedirs(art_dir, exist_ok=True)
                try:
                    url = self.print_version(article.url)
                except NotImplementedError:
                    url = article.url
                except Exception:
                    self.log.exception("Failed to find print version for: " + str(article.url))
                    url = None
                if not url:
                    continue
                if self.use_embedded_content or (self.use_embedded_content is None and feed.has_embedded_content()):
                    func, arg = self.fetch_embedded_article, article
                elif self.articles_are_obfuscated:
                    func, arg = self.fetch_obfuscated_article, url
                else:
                    func, arg = self.fetch_article, url
                jobs.append((func, arg, art_dir, f, a, len(feed), feed, article))

        self.jobs_done = 0
        n = len(jobs)
        self.log("Starting download [%d threads]..." % min(self.simultaneous_downloads, max(1, n)))

        def run_job(job):
            func, arg, art_dir, f, a, feed_len, feed, article = job
            try:
                res = func(arg, art_dir, f, a, feed_len)
                return ("ok", res, f, a, feed, article, None)
            except AbortArticle as e:
                return ("abort", None, f, a, feed, article, str(e))
            except Exception:
                return ("error", None, f, a, feed, article, traceback.format_exc())

        workers = max(1, min(self.simultaneous_downloads, n)) if n else 1
        with ThreadPoolExecutor(max_workers=workers) as tp:
            for status, res, f, a, feed, article, err in tp.map(run_job, jobs):
                self.jobs_done += 1
                article.orig_url = article.url
                if status == "ok":
                    article.url = "article_%d/index.xhtml" % a
                    article.downloaded = True
                    self.report_progress(float(self.jobs_done) / max(1, n), _("Article downloaded: %s") % article.title)
                elif status == "abort":
                    self.log.warning("Aborted download of article:", article.title, "from", article.orig_url)
                    self.aborted_articles.append((feed, article))
                    self.report_progress(float(self.jobs_done) / max(1, n), _("Article download aborted: %s") % article.title)
                else:
                    self.log.error("Failed to download article:", article.title, "from", article.orig_url)
                    self.log.debug(err)
                    self.failed_downloads.append((feed, article, err))
                    self.report_progress(float(self.jobs_done) / max(1, n), _("Article download failed: %s") % article.title)

        for f, feed in enumerate(feeds):
            feed_dir = os.path.join(self.output_dir, "feed_%d" % f)
            with open(os.path.join(feed_dir, "index.xhtml"), "wb") as fi:
                fi.write(self.feed2index(f, feeds))
        self.create_opf(feeds)
        self.report_progress(1, _("Feeds downloaded to %s") % index)
        return index

    def download(self) -> str:
        try:
            res = self.build_index()
            self.report_progress(1, _("Download finished"))
            if self.failed_downloads:
                self.log.warning(_("Failed to download the following articles:"))
                for feed, article, debug in self.failed_downloads:
                    self.log.warning(article.title, "from", feed.title)
            return res
        finally:
            self.cleanup()

    # --- OPF / NCX (metadata for the EPUB) ----------------------------------------

    def create_opf(self, feeds: list, dir: str | None = None) -> None:
        from .epub import write_opf_and_ncx

        write_opf_and_ncx(self, feeds, dir or self.output_dir)


def _write_temp(data: bytes) -> str:
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".html")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def _esc(s) -> str:
    from xml.sax.saxutils import escape

    return escape(str(s))
