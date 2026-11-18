"""Load calibre ``.recipe`` files unmodified.

A shim installs fake ``calibre.*`` modules in sys.modules that re-export this
engine, so recipes doing ``from calibre.web.feeds.news import BasicNewsRecipe``
(or ``from calibre.utils.localization import _`` etc.) run without changes.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import types

_SHIM_INSTALLED = False


def install_calibre_shim() -> None:
    global _SHIM_INSTALLED
    if _SHIM_INSTALLED:
        return
    from . import feeds as feeds_mod
    from . import recipe as recipe_mod

    calibre = types.ModuleType("calibre")
    calibre.__appname__ = "calibre-news"
    calibre.__version__ = "0.1.0"
    calibre.browser = lambda *a, **kw: recipe_mod.Browser(*a, **kw)
    calibre.iswindows = False
    calibre.strftime = lambda fmt, t=None: __import__("time").strftime(fmt, t or __import__("time").localtime())

    web = types.ModuleType("calibre.web")
    web.Recipe = recipe_mod.BasicNewsRecipe

    feeds_pkg = types.ModuleType("calibre.web.feeds")
    feeds_pkg.Feed = feeds_mod.Feed
    feeds_pkg.Article = feeds_mod.Article
    feeds_pkg.feed_from_xml = feeds_mod.feed_from_xml
    feeds_pkg.feeds_from_index = feeds_mod.feeds_from_index

    news = types.ModuleType("calibre.web.feeds.news")
    news.BasicNewsRecipe = recipe_mod.BasicNewsRecipe
    news.CustomIndexRecipe = recipe_mod.BasicNewsRecipe
    news.AutomaticNewsRecipe = recipe_mod.BasicNewsRecipe
    news.CalibrePeriodical = recipe_mod.BasicNewsRecipe
    news.classes = recipe_mod.classes
    news.prefixed_classes = recipe_mod.prefixed_classes
    news.LoginFailed = ValueError
    news.DownloadDenied = ValueError

    utils = types.ModuleType("calibre.utils")
    localization = types.ModuleType("calibre.utils.localization")
    localization._ = lambda s: s
    localization.ngettext = lambda s, p, n: s if n == 1 else p
    localization.canonicalize_lang = lambda l: l

    ebooks = types.ModuleType("calibre.ebooks")
    bs_module = types.ModuleType("calibre.ebooks.BeautifulSoup")
    from bs4 import BeautifulSoup as _BS
    from bs4 import CData, Comment, Doctype, NavigableString, Tag

    bs_module.BeautifulSoup = _BS
    bs_module.CData = CData
    bs_module.Comment = Comment
    bs_module.Doctype = Doctype
    bs_module.NavigableString = NavigableString
    bs_module.Tag = Tag

    for name, mod in {
        "calibre": calibre,
        "calibre.web": web,
        "calibre.web.feeds": feeds_pkg,
        "calibre.web.feeds.news": news,
        "calibre.utils": utils,
        "calibre.utils.localization": localization,
        "calibre.ebooks": ebooks,
        "calibre.ebooks.BeautifulSoup": bs_module,
    }.items():
        sys.modules.setdefault(name, mod)
    calibre.web = web
    calibre.utils = utils
    calibre.ebooks = ebooks
    web.feeds = feeds_pkg
    feeds_pkg.news = news
    _SHIM_INSTALLED = True


def load_recipe_class(path: str):
    """Load a .recipe file and return its BasicNewsRecipe subclass."""
    install_calibre_shim()
    from .recipe import BasicNewsRecipe

    path = os.path.abspath(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    mod_name = "newsrecipe_%s_%d" % (stem, abs(hash(path)) % 100000)
    loader = importlib.machinery.SourceFileLoader(mod_name, path)
    spec = importlib.util.spec_from_file_location(mod_name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, BasicNewsRecipe) and value is not BasicNewsRecipe and value.__module__ == mod_name:
            return value
    raise ValueError("No BasicNewsRecipe subclass found in %s" % path)


def list_recipe_files(recipes_dir: str) -> list[str]:
    if not os.path.isdir(recipes_dir):
        return []
    return sorted(os.path.join(recipes_dir, f) for f in os.listdir(recipes_dir) if f.endswith(".recipe"))
