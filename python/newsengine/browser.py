"""Minimal mechanize-like browser used by recipes, implemented on urllib.

Recipes that override ``get_browser()`` expect an object with an
``addheaders`` list and an ``open()`` method returning a file-like response
with ``read()``, ``geturl()`` and ``info()``. This module provides exactly
that subset so unmodified calibre recipes work unchanged.
"""

from __future__ import annotations

import gzip
import http.cookiejar
import io
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Mobile Safari/537.36"
)


class Response(io.BytesIO):
    """BytesIO with the mechanize-style response API."""

    def __init__(self, data: bytes, url: str, headers) -> None:
        super().__init__(data)
        self._url = url
        self._headers = headers

    def geturl(self) -> str:
        return self._url

    def info(self):
        return self._headers

    def getheader(self, name: str, default=None):
        return self._headers.get(name, default)


class Browser:
    """Small cookie-aware HTTP browser with a mechanize-compatible surface."""

    def __init__(self, user_agent: str | None = None, timeout: float = 120.0, verify_ssl_certificates: bool = False) -> None:
        self.addheaders: list[tuple[str, str]] = [("User-Agent", user_agent or DEFAULT_USER_AGENT)]
        self.timeout = timeout
        self._handle_gzip = True
        self._cookies = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context()
        if not verify_ssl_certificates:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._opener = build_opener(HTTPCookieProcessor(self._cookies), HTTPSHandler(context=ctx))

    def set_handle_gzip(self, val: bool) -> None:
        self._handle_gzip = bool(val)

    def set_cookie(self, name: str, value: str, domain: str = "", path: str = "/") -> None:
        self._cookies.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=domain or ".example.com",
                domain_specified=bool(domain),
                domain_initial_dot=domain.startswith("."),
                path=path,
                path_specified=True,
                secure=False,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )

    def open(self, url: str, data=None, timeout: float | None = None, **kwargs):
        if isinstance(data, dict):
            data = urlencode(data).encode("utf-8")
        headers = {}
        for k, v in self.addheaders:
            headers[k] = v
        if self._handle_gzip and "Accept-Encoding" not in headers:
            headers["Accept-Encoding"] = "gzip"
        req = Request(url, data=data, headers=headers)
        try:
            resp = self._opener.open(req, timeout=timeout or self.timeout)
        except HTTPError as err:
            raise URLError(err.reason) from err
        raw = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        return Response(raw, resp.geturl(), resp.headers)

    def open_novisit(self, url: str, data=None, timeout: float | None = None, **kwargs):
        return self.open(url, data=data, timeout=timeout, **kwargs)

    def clone_browser(self) -> "Browser":
        br = Browser(timeout=self.timeout)
        br.addheaders = list(self.addheaders)
        br._handle_gzip = self._handle_gzip
        br._cookies = self._cookies
        return br


def browser(*args, **kwargs) -> Browser:
    """Factory mirroring ``calibre.browser()``."""
    return Browser(*args, **kwargs)
