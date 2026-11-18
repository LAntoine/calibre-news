"""Offline end-to-end test of the engine: local HTTP server + test recipe -> EPUB."""

from __future__ import annotations

import os
import zipfile

from newsengine import run_recipe


def test_full_pipeline_offline(test_recipe_file, tmp_path, epub_validator):
    out = str(tmp_path / "test_paper.epub")
    result = run_recipe(test_recipe_file, out, work_dir=str(tmp_path / "work"), verbose=1)
    assert result.ok, result.error
    assert result.title == "Test Paper"
    assert result.feeds == 1
    # duplicate (title+url) article removed -> 2 unique articles
    assert result.articles_downloaded == 2, result.articles_failed
    assert result.articles_failed == []

    info = epub_validator(out)
    assert info["title"] == "Test Paper"
    assert info["language"] == "en"
    assert info["navpoints"] >= 3  # index + feed + 2 articles

    with zipfile.ZipFile(out) as zf:
        art = zf.read("OEBPS/feed_0/article_0/index.xhtml").decode("utf-8")
        # keep_only_tags kept the h1 and content div
        assert "Article One" in art
        assert "First paragraph of article one." in art
        # remove_tags removed the ad div
        assert "ADVERTISEMENT" not in art
        # remove_javascript removed the script
        assert "evil()" not in art
        # no_stylesheets removed the original <style>
        assert "color:red" not in art
        # image was downloaded and localized
        assert 'src="images/img1.png"' in art
        assert "OEBPS/feed_0/article_0/images/img1.png" in info["names"]
        # navbar links back to the index
        assert "../../index.xhtml" in art
        # index page lists both articles
        index = zf.read("OEBPS/index.xhtml").decode("utf-8")
        assert "feed_0/index.xhtml" in index
        feed_index = zf.read("OEBPS/feed_0/index.xhtml").decode("utf-8")
        assert "Article One" in feed_index and "Article Two" in feed_index
        # extra css (template_css) injected
        assert "calibre_navbar" in art


def test_recipe_hooks_called(tmp_path, news_server):
    import builtins

    calls = []
    builtins.calls = calls
    src = """from calibre.web.feeds.news import BasicNewsRecipe

class HookPaper(BasicNewsRecipe):
    title = 'Hook Paper'
    language = 'en'
    oldest_article = 7
    no_stylesheets = True
    feeds = [('News', '%s/feed.xml')]
    def preprocess_raw_html(self, raw_html, url):
        __import__('builtins').calls.append(('raw', url))
        return raw_html
    def preprocess_html(self, soup):
        __import__('builtins').calls.append(('html', None))
        return soup
    def get_article_url(self, article):
        __import__('builtins').calls.append(('url', article.get('link')))
        return BasicNewsRecipe.get_article_url(self, article)
    def postprocess_html(self, soup, first):
        __import__('builtins').calls.append(('post', first))
        return soup
""" % (
        news_server,
    )
    p = tmp_path / "hook_paper.recipe"
    p.write_text(src, encoding="utf-8")
    try:
        result = run_recipe(str(p), str(tmp_path / "hook.epub"), work_dir=str(tmp_path / "w2"))
        assert result.ok, result.error
        kinds = [k for k, _v in calls]
        assert "raw" in kinds and "html" in kinds and "url" in kinds and "post" in kinds
        assert any(str(v).endswith("/article1.html") for k, v in calls if k == "url")
        assert any(v is True for k, v in calls if k == "post")
    finally:
        del builtins.calls
