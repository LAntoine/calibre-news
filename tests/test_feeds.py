from __future__ import annotations

import time

from newsengine.feeds import Article, Feed, feed_from_xml, feeds_from_index

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Sec</title>
<item><title>A</title><link>http://x/1</link><pubDate>%s</pubDate></item>
<item><title>B</title><link>http://x/2</link><pubDate>%s</pubDate></item>
</channel></rss>"""

OLD_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Sec</title>
<item><title>Old</title><link>http://x/old</link><pubDate>Mon, 01 Jan 2001 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def _date(days_ago=0):
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() - days_ago * 86400))


def test_feed_from_xml_parses_articles():
    feed = feed_from_xml(RSS % (_date(), _date()), title="My Section")
    assert feed.title == "My Section"
    assert len(feed) == 2
    assert [a.title for a in feed] == ["A", "B"]
    assert feed.articles[0].url == "http://x/1"


def test_oldest_article_filter():
    feed = feed_from_xml(OLD_RSS, oldest_article=7)
    assert len(feed) == 0
    feed = feed_from_xml(OLD_RSS, oldest_article=100000)
    assert len(feed) == 1


def test_max_articles_per_feed():
    feed = feed_from_xml(RSS % (_date(), _date()), max_articles_per_feed=1)
    assert len(feed) == 1


def test_get_article_url_hook():
    def hook(item):
        return "http://custom/" + item["link"].rsplit("/", 1)[-1]

    feed = feed_from_xml(RSS % (_date(), _date()), get_article_url=hook)
    assert feed.articles[0].url == "http://custom/1"


def test_origlink_preferred():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>S</title>
    <item><title>A</title><link>http://feedburner/redir</link>
    <feedburner:origlink>http://real/1</feedburner:origlink>
    <pubDate>%s</pubDate></item></channel></rss>""" % _date()
    from newsengine.recipe import BasicNewsRecipe

    r = BasicNewsRecipe.__new__(BasicNewsRecipe)
    feed = feed_from_xml(xml, get_article_url=r.get_article_url)
    assert feed.articles[0].url == "http://real/1"


def test_feeds_from_index():
    index = [("Sec1", [{"id": "1", "title": "T", "url": "http://x", "timestamp": time.time()}])]
    feeds = feeds_from_index(index, oldest_article=7)
    assert len(feeds) == 1
    assert len(feeds[0]) == 1


def test_article_text_summary_strips_html():
    a = Article("1", "T", "http://x", "me", "<p>Hello <b>world</b></p>", time.gmtime())
    assert a.text_summary == "Hello world"


def test_feed_reverse_and_remove():
    feed = feed_from_xml(RSS % (_date(), _date()))
    feed.reverse()
    assert feed.articles[0].title == "B"
    art = feed.articles[0]
    feed.remove_article(art)
    assert len(feed) == 1
