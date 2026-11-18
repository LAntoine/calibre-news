"""High-level API used by the Android app (via Chaquopy) and by the tests.

Example::

    from newsengine import run_recipe, list_recipes
    result = run_recipe("recipes/le_monde.recipe", output_path="/sdcard/Download/Le Monde.epub")
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from .epub import write_epub
from .log import Log
from .recipe import BasicNewsRecipe, RecipeOptions
from .recipes import list_recipe_files, load_recipe_class


@dataclass
class RecipeInfo:
    path: str
    name: str
    title: str
    author: str
    description: str
    language: str
    feeds: list


@dataclass
class RunResult:
    ok: bool
    recipe: str
    title: str = ""
    epub_path: str | None = None
    work_dir: str | None = None
    feeds: int = 0
    articles_downloaded: int = 0
    articles_failed: list = field(default_factory=list)
    articles_aborted: list = field(default_factory=list)
    error: str | None = None
    log: list = field(default_factory=list)


def _recipe_info(path: str) -> RecipeInfo:
    cls = load_recipe_class(path)
    inst = cls(RecipeOptions())
    return RecipeInfo(
        path=os.path.abspath(path),
        name=os.path.splitext(os.path.basename(path))[0],
        title=str(inst.title),
        author=str(inst.__author__),
        description=str(inst.description),
        language=str(inst.language),
        feeds=list(inst.feeds or []),
    )


def list_recipes(recipes_dir: str) -> list:
    return [_recipe_info(p) for p in list_recipe_files(recipes_dir)]


def list_recipes_dict(recipes_dir: str) -> list:
    """list_recipes() as plain dicts (Chaquopy converts these to java.util.Map/List)."""
    return [
        {
            "path": i.path,
            "name": i.name,
            "title": i.title,
            "author": i.author,
            "description": i.description,
            "language": i.language,
            "feeds": [list(f) if isinstance(f, (tuple, list)) else f for f in i.feeds],
        }
        for i in list_recipes(recipes_dir)
    ]


def run_recipe_dict(recipe, output_path, options=None) -> dict:
    """run_recipe() with a plain dict of options and a plain dict result (Chaquopy-friendly)."""
    options = dict(options or {})
    r = run_recipe(recipe, output_path, **options)
    return {
        "ok": r.ok,
        "recipe": r.recipe,
        "title": r.title,
        "epub_path": r.epub_path,
        "work_dir": r.work_dir,
        "feeds": r.feeds,
        "articles_downloaded": r.articles_downloaded,
        "articles_failed": [list(x) for x in r.articles_failed],
        "articles_aborted": list(r.articles_aborted),
        "error": r.error,
        "log": [[level, msg] for level, msg in r.log],
    }


def _safe_filename(title: str) -> str:
    name = re.sub(r"[^\w\s\-().]", "", title, flags=re.UNICODE).strip()
    return (name or "news") + ".epub"


def run_recipe(
    recipe,
    output_path: str | None = None,
    *,
    recipes_dir: str | None = None,
    work_dir: str | None = None,
    max_feeds: int | None = None,
    max_articles_per_feed: int | None = None,
    oldest_article: float | None = None,
    username: str | None = None,
    password: str | None = None,
    verbose: int = 0,
    progress_callback: Callable | None = None,
    log_callback: Callable | None = None,
) -> RunResult:
    """Run a recipe (path to a .recipe file, or a recipe class) and produce an EPUB.

    :param recipe: path to a ``.recipe`` file or a BasicNewsRecipe subclass.
    :param output_path: where to write the .epub (default: <title>.epub in cwd).
    :param work_dir: scratch directory for the intermediate XHTML tree.
    :param max_feeds / max_articles_per_feed: limits, mainly for tests (like calibre's --test).
    """
    log = Log(callback=log_callback)
    try:
        if isinstance(recipe, str):
            path = recipe
            if not os.path.isabs(path) and recipes_dir:
                cand = os.path.join(recipes_dir, recipe)
                if os.path.isfile(cand):
                    path = cand
            if not os.path.isfile(path) and not path.endswith(".recipe") and recipes_dir:
                path = os.path.join(recipes_dir, path + ".recipe")
            cls = load_recipe_class(path)
            recipe_name = os.path.splitext(os.path.basename(path))[0]
        else:
            cls = recipe
            recipe_name = getattr(cls, "__name__", "recipe")

        opts = RecipeOptions(verbose=verbose, username=username, password=password)
        if max_feeds is not None or max_articles_per_feed is not None:
            opts.test = (max_feeds or 999, max_articles_per_feed or 999)
        inst = cls(opts, log=log, progress_reporter=progress_callback)
        if oldest_article is not None:
            inst.oldest_article = oldest_article

        work = work_dir or tempfile.mkdtemp(prefix="calibre-news-")
        os.makedirs(work, exist_ok=True)
        inst.output_dir = work

        inst.download()

        if output_path is None:
            output_path = os.path.abspath(_safe_filename(inst.short_title()))
        write_epub(work, output_path)

        feeds = getattr(inst, "feed_objects", [])
        downloaded = [a for f in feeds for a in f if getattr(a, "downloaded", False)]
        failed = [(a.title, a.orig_url or a.url) for _f, a, _tb in getattr(inst, "failed_downloads", [])]
        aborted = [a.title for _f, a in getattr(inst, "aborted_articles", [])]
        ok = bool(downloaded)
        return RunResult(
            ok=ok,
            recipe=recipe_name,
            title=str(inst.title),
            epub_path=output_path if ok else None,
            work_dir=work,
            feeds=len(feeds),
            articles_downloaded=len(downloaded),
            articles_failed=failed,
            articles_aborted=aborted,
            error=None if ok else "No articles could be downloaded",
            log=list(log.records),
        )
    except Exception as e:
        import traceback

        return RunResult(
            ok=False,
            recipe=str(recipe),
            error="%s: %s" % (type(e).__name__, e),
            log=list(log.records) + [("ERROR", traceback.format_exc())],
        )
