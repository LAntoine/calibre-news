# calibre-news

An Android app that runs **calibre news recipes** on the phone and produces **EPUB** files,
with support for **scheduled (daily) downloads** of any recipe/newspaper.

It is a port of calibre's "Fetch news" feature (`calibre.web.feeds.news.BasicNewsRecipe`)
to Android: recipes stay **unmodified Python** and are executed by an embedded CPython.

## How it works (architecture)

```
┌──────────────────────────────┐         ┌─────────────────────────────────────┐
│  Kotlin / Android app        │         │  Embedded Python (Chaquopy, CPython) │
│  android/                    │  calls  │  python/newsengine/                  │
│  - UI (Compose)              │ ──────► │  - recipe.py   BasicNewsRecipe port  │
│  - WorkManager scheduling    │  PyObject│  - feeds.py    RSS/Atom parsing      │
│  - Room (schedules)          │         │  - fetcher.py  article download      │
│  - Notifications, MediaStore │         │  - epub.py     EPUB 2.0.1 writer     │
└──────────────────────────────┘         │  - recipes.py  loads .recipe files   │
                                         │    (installs a calibre.* shim)       │
                                         │  recipes/*.recipe  (e.g. le_monde)   │
                                         └─────────────────────────────────────┘
```

### Running Python recipes on Android

[Chaquopy](https://chaquo.com/chaquopy/) (`com.chaquo.python` Gradle plugin, see
`android/app/build.gradle.kts`) embeds CPython 3.13 in the APK. The `python/` directory is
added to the app's Python source set, and pip dependencies (`feedparser`, `beautifulsoup4`,
`python-dateutil`) are installed into the APK at build time.

Recipes are **not ported**: `newsengine/recipes.py` installs a shim that maps the
`calibre.web.feeds.news` import (and a few other `calibre.*` modules) onto the portable
engine, so a stock `.recipe` file from calibre's `recipes/` directory runs unmodified.
`recipes/le_monde.recipe` is a verbatim copy of the upstream recipe; the integration test
also runs the file directly from the upstream calibre checkout when available
(`CALIBRE_REPO` env var).

### The news pipeline (port of calibre's)

`newsengine.recipe.BasicNewsRecipe` mirrors calibre's class and hooks
(`feeds`, `get_article_url`, `preprocess_raw_html`, `preprocess_html`, `keep_only_tags`,
`remove_tags`, `extra_css`, `no_stylesheets`, `ignore_duplicate_articles`,
`get_cover_url`, `get_browser`, ...). The pipeline is the same as calibre's:

1. fetch + parse RSS/Atom feeds (`feedparser`),
2. filter by `oldest_article` / `max_articles_per_feed`, remove duplicates,
3. download the cover (`get_cover_url`),
4. download each article with recipe hooks applied in calibre's order
   (raw HTML → `preprocess_raw_html` → regexps → `skip_ad_pages` → `keep_only_tags` →
   `remove_tags_after/before` → `remove_tags` → `preprocess_html` → images downloaded and
   localized → navbar + `extra_css` injected → JavaScript stripped → HTML5 tags nuked),
5. write index pages + OPF + NCX, package everything into an EPUB.

HTTP is done by a small mechanize-compatible `Browser` (`newsengine/browser.py`, urllib +
cookies + gzip), so recipes that override `get_browser()` and set `addheaders` (like
Le Monde's mobile-API token) work unchanged.

### EPUB conversion

calibre's HTML→EPUB conversion uses C extensions that cannot be built for Android.
Instead, `newsengine/epub.py` writes **EPUB 2.0.1** in pure Python: article pages are
serialized as strict XHTML (own serializer in `newsengine/souputil.py`), then zipped with
`mimetype` first (stored), `META-INF/container.xml`, `content.opf` (manifest/spine/guide,
cover) and `toc.ncx`. No native dependencies. Image resizing/compression (Pillow) is
optional and off by default; unknown image formats are passed through.

### Scheduling

`android/.../work/Scheduler.kt` stores per-recipe daily schedules in Room and enqueues
unique `OneTimeWorkRequest`s with WorkManager (network + battery-not-low constraints,
re-enqueued for the next day after each run, restored after reboot via `BootReceiver`).
`NewsDownloadWorker` runs the recipe through Chaquopy, posts a progress/completion
notification, and exports the EPUB to the public Downloads folder (MediaStore).

## Repository layout

```
python/newsengine/   portable engine (runs on desktop CPython and on Android/Chaquopy)
recipes/             .recipe files bundled into the app (le_monde.recipe vendored verbatim)
tests/               pytest suite; tests/test_integration_le_monde.py runs Le Monde live
android/             Android Studio project (Kotlin + Compose + Chaquopy + WorkManager + Room)
```

## Running the tests

```bash
pip install -e '.[test]'
pytest                      # offline tests + live Le Monde integration test
pytest -m "not integration" # offline only
NEWSENGINE_FULL=1 pytest tests/test_integration_le_monde.py   # full Le Monde run, all feeds
CALIBRE_REPO=/path/to/calibre pytest tests/test_integration_le_monde.py  # also run upstream recipe
```

The Le Monde integration test downloads the real feeds from lemonde.fr (bounded to
2 feeds x 3 articles by default), builds `le_monde.epub`, and validates the EPUB structure
(mimetype/container/OPF/NCX/manifest/spine, well-formed XHTML, localized images, cover).

## Building the Android app

```bash
cd android
./gradlew assembleDebug    # first run: run `gradle wrapper` once if the wrapper jar is missing
```

The Gradle build copies `../recipes/*.recipe` into the APK assets and pip-installs the
Python dependencies via Chaquopy. Open the app, pick a newspaper, "Download now" or
enable a daily schedule; the EPUB lands in `Downloads/calibre-news/`.

## Recipe compatibility

Supported recipe surface: all common `BasicNewsRecipe` attributes and hooks listed above,
`get_browser()` overrides that only touch `addheaders`/cookies, `browser_user_agent`,
embedded content, obfuscated articles, `parse_index()` recipes. Not supported (would need
a real browser or native code): `browser_type = 'qt' | 'webengine' | 'camoufox'`
(JS-rendered sites), `auto_cleanup` readability extraction, image rescaling without Pillow,
login via HTML forms (mechanize-style `select_form`).
