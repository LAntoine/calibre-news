"""Integration test: run the real Le Monde recipe (live network) and validate the EPUB.

Bounded by default (2 feeds x 3 articles) so CI stays fast; set
NEWSENGINE_FULL=1 for a full run of every feed/article.
"""

from __future__ import annotations

import os
import zipfile

import pytest

from newsengine import run_recipe

RECIPES_DIR = os.path.join(os.path.dirname(__file__), "..", "recipes")
LE_MONDE = os.path.join(RECIPES_DIR, "le_monde.recipe")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def le_monde_result(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("le_monde") / "le_monde.epub")
    full = os.environ.get("NEWSENGINE_FULL") == "1"
    result = run_recipe(
        LE_MONDE,
        out,
        work_dir=str(tmp_path_factory.mktemp("le_monde_work")),
        max_feeds=None if full else 2,
        max_articles_per_feed=None if full else 3,
        oldest_article=2,
        verbose=1,
    )
    result._epub_path = out  # keep path even on failure for debugging
    return result


def test_le_monde_recipe_loads():
    from newsengine import load_recipe_class

    cls = load_recipe_class(LE_MONDE)
    assert cls.title == "Le Monde"
    assert cls.language == "fr"
    assert len(cls.feeds) == 5


def test_le_monde_download_produces_valid_epub(le_monde_result, epub_validator):
    result = le_monde_result
    assert result.ok, "run failed: %s\n%s" % (result.error, "\n".join(m for _l, m in result.log[-20:]))
    assert result.title == "Le Monde"
    assert result.feeds >= 1
    assert result.articles_downloaded >= 1, "no articles downloaded; failed: %s" % result.articles_failed
    info = epub_validator(result._epub_path)
    assert info["title"].startswith("Le Monde")
    assert info["language"] == "fr"
    # articles and their images are inside the epub
    article_files = [n for n in info["names"] if "/article_" in n and n.endswith("index.xhtml")]
    assert len(article_files) == result.articles_downloaded
    image_files = [n for n in info["names"] if "/images/" in n]
    assert image_files, "no images downloaded into the epub"
    with zipfile.ZipFile(result._epub_path) as zf:
        cover = [n for n in info["names"] if n.startswith("OEBPS/cover.")]
        # the cover URL is date-based and can 404 (e.g. clock skew); then the engine logs a warning instead
        assert cover or any("did not return an image" in m for _l, m in result.log), (
            "Le Monde recipe provides get_cover_url, cover should be present"
        )
        body = zf.read(article_files[0]).decode("utf-8")
        assert "<body" in body and len(body) > 500


def test_unmodified_upstream_recipe_runs(tmp_path):
    """The recipe file in the upstream calibre repo must run unmodified via the shim."""
    calibre_repo = os.environ.get(
        "CALIBRE_REPO", os.path.join(os.path.dirname(__file__), "..", "..", "github__LAntoine__calibre")
    )
    upstream = os.path.join(calibre_repo, "recipes", "le_monde.recipe")
    if not os.path.isfile(upstream):
        pytest.skip("upstream calibre repo not available at %s" % calibre_repo)
    result = run_recipe(
        upstream,
        str(tmp_path / "upstream.epub"),
        work_dir=str(tmp_path / "w"),
        max_feeds=1,
        max_articles_per_feed=1,
        oldest_article=2,
    )
    assert result.ok, result.error
    assert result.articles_downloaded >= 1
