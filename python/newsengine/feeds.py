"""Feed parsing: Article / Feed model plus feedparser-based parsing.

Port of calibre.web.feeds, trimmed to what recipes need.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Callable, Iterable

from .log import Log, default_log


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_datetime(published) -> datetime:
    if published is None:
        return _utcnow()
    if isinstance(published, time.struct_time):
        return datetime(*published[:6], tzinfo=timezone.utc)
    if isinstance(published, datetime):
        if published.tzinfo is None:
            return published.replace(tzinfo=timezone.utc)
        return published.astimezone(timezone.utc)
    from dateutil.parser import parse as parse_date

    return parse_date(str(published)).astimezone(timezone.utc)


class Article:
    """A single article from a feed."""

    def __init__(self, id, title, url, author, summary, published, content=None) -> None:
        self.downloaded = False
        self.id = id
        if not title or not isinstance(title, (str, bytes)):
            title = "Unknown"
        if isinstance(title, bytes):
            title = title.decode("utf-8", "replace")
        self._title = re.sub(r"\s+", " ", title).strip()
        self.url = url
        self.author = author.decode("utf-8", "replace") if isinstance(author, bytes) else author
        self.toc_thumbnail = None
        self.internal_toc_entries = ()
        if isinstance(summary, bytes):
            summary = summary.decode("utf-8", "replace")
        self.summary = summary
        self.text_summary = self._text_of(summary) if summary else summary
        self.content = content
        self.date = published
        self.utctime = _to_datetime(published)
        self.localtime = self.utctime.astimezone()
        self._formatted_date = None
        self.orig_url = url
        self.sub_pages: list[str] = []

    @staticmethod
    def _text_of(html_text):
        if html_text and "<" in html_text:
            try:
                from bs4 import BeautifulSoup

                return BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)
            except Exception:
                return re.sub(r"<.+?>", "", html_text)
        return html_text

    @property
    def formatted_date(self) -> str:
        if self._formatted_date is None:
            self._formatted_date = self.localtime.strftime(" [%a, %d %b %H:%M]")
        return self._formatted_date

    @formatted_date.setter
    def formatted_date(self, val) -> None:
        if isinstance(val, str):
            self._formatted_date = val

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, val) -> None:
        if isinstance(val, bytes):
            val = val.decode("utf-8", "replace")
        self._title = str(val)

    def __repr__(self) -> str:
        return "Title: %s\nURL: %s\nAuthor: %s\nDate: %s\n" % (
            self.title,
            self.url,
            self.author,
            self.localtime.strftime("%a, %d %b, %Y %H:%M"),
        )

    def __str__(self) -> str:
        return repr(self)

    def is_same_as(self, other) -> bool:
        if self.url:
            return self.url == getattr(other, "url", False)
        return self.content == getattr(other, "content", False)


class Feed:
    """A parsed feed (one section of a newspaper)."""

    def __init__(self, get_article_url: Callable | None = None, log: Log = default_log) -> None:
        self.logger = log
        self.get_article_url = get_article_url or (lambda item: item.get("link", None))
        self.title = ""
        self.description = ""
        self.image_url = None
        self.image_width = 88
        self.image_height = 31
        self.image_alt = ""
        self.articles: list[Article] = []
        self.id_counter = 0
        self.added_articles: list = []
        self.oldest_article = 7

    def populate_from_feed(self, feed, title=None, oldest_article=7, max_articles_per_feed=100) -> None:
        entries = feed.entries
        feed_meta = feed.feed
        self.title = feed_meta.get("title", "Unknown section") if not title else title
        self.description = feed_meta.get("description", "")
        image = feed_meta.get("image", {})
        self.image_url = image.get("href", None)
        self.image_width = image.get("width", 88)
        self.image_height = image.get("height", 31)
        self.image_alt = image.get("title", "")
        self.articles: list[Article] = []
        self.id_counter = 0
        self.added_articles: list = []
        self.oldest_article = oldest_article
        for item in entries:
            if len(self.articles) >= max_articles_per_feed:
                break
            self.parse_article(item, max_articles_per_feed)

    def populate_from_preparsed_feed(self, title, articles: Iterable, oldest_article=7, max_articles_per_feed=100) -> None:
        self.title = str(title or "Unknown feed")
        self.description = ""
        self.image_url = None
        self.articles = []
        self.added_articles = []
        self.oldest_article = oldest_article
        self.id_counter = 0
        for item in articles:
            if len(self.articles) >= max_articles_per_feed:
                break
            self.id_counter += 1
            id = item.get("id", None) or "internal id#%d" % self.id_counter
            if id in self.added_articles:
                return
            self.added_articles.append(id)
            published = time.gmtime(item.get("timestamp", time.time()))
            article = Article(
                id,
                item.get("title", "Untitled article"),
                item.get("url", None),
                item.get("author", ""),
                item.get("description", ""),
                published,
                item.get("content", ""),
            )
            delta = _utcnow() - article.utctime
            if delta.total_seconds() <= 24 * 3600 * self.oldest_article:
                self.articles.append(article)
            d = item.get("date", "")
            article.formatted_date = d

    def parse_article(self, item, max_articles_per_feed=100) -> None:
        self.id_counter += 1
        id = item.get("id", None) or "internal id#%d" % self.id_counter
        if id in self.added_articles:
            return
        published = None
        for date_field in ("date_parsed", "published_parsed", "updated_parsed"):
            published = item.get(date_field, None)
            if published is not None:
                break
        if not published:
            from dateutil.parser import parse as parse_date

            for date_field in ("date", "published", "updated"):
                try:
                    published = parse_date(item[date_field])
                    break
                except Exception:
                    continue
        if not published:
            published = time.gmtime()
        self.added_articles.append(id)

        title = item.get("title", "Untitled article")
        if isinstance(title, str) and title.startswith("<"):
            title = re.sub(r"<.+?>", "", title)
        try:
            link = self.get_article_url(item)
        except Exception:
            self.logger.warning("Failed to get link for %s" % title)
            link = None

        description = item.get("summary", None)
        author = item.get("author", None)

        content = [i.value for i in item.get("content", []) if i.value]
        content = [i if isinstance(i, str) else i.decode("utf-8", "replace") for i in content]
        content = "\n".join(content)
        if not content.strip():
            content = None
        if not link and not content:
            return
        article = Article(id, title, link, author, description, published, content)
        delta = _utcnow() - article.utctime
        if delta.total_seconds() <= 24 * 3600 * self.oldest_article:
            self.articles.append(article)
        else:
            self.logger.debug(
                "Skipping article %s (%s) from feed %s as it is too old."
                % (title, article.localtime.strftime("%a, %d %b, %Y %H:%M"), self.title)
            )

    def reverse(self) -> None:
        self.articles.reverse()

    def __iter__(self):
        return iter(self.articles)

    def __len__(self) -> int:
        return len(self.articles)

    def has_embedded_content(self) -> bool:
        length = 0
        for a in self:
            if a.content or a.summary:
                length += max(len(a.content or ""), len(a.summary or ""))
        return length > 2000 * len(self)

    def has_article(self, article: Article) -> bool:
        return any(a.is_same_as(article) for a in self)

    def find(self, article: Article) -> int:
        for i, a in enumerate(self):
            if a.is_same_as(article):
                return i
        return -1

    def remove_article(self, article: Article) -> None:
        try:
            self.articles.remove(article)
        except ValueError:
            pass


def feed_from_xml(
    raw_xml: bytes,
    title=None,
    oldest_article=7,
    max_articles_per_feed=100,
    get_article_url: Callable | None = None,
    log: Log = default_log,
) -> Feed:
    from feedparser import parse

    if isinstance(raw_xml, str):
        raw_xml = raw_xml.encode("utf-8")
    # Handle unclosed escaped entities, they trip up feedparser
    raw_xml = re.sub(rb"(&amp;#\d+)([^0-9;])", rb"\1;\2", raw_xml)
    feed = parse(raw_xml)
    pfeed = Feed(get_article_url=get_article_url, log=log)
    pfeed.populate_from_feed(feed, title=title, oldest_article=oldest_article, max_articles_per_feed=max_articles_per_feed)
    return pfeed


def feeds_from_index(index, oldest_article=7, max_articles_per_feed=100, log: Log = default_log) -> list:
    feeds = []
    for title, articles in index:
        pfeed = Feed(log=log)
        pfeed.populate_from_preparsed_feed(title, articles, oldest_article=oldest_article, max_articles_per_feed=max_articles_per_feed)
        feeds.append(pfeed)
    return feeds
