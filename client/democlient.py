"""A tiny client for the cookie demo. Standard library only.

It behaves like a browser that keeps a cookie jar, except that it lets the caller
look at the first response before any redirect is followed. That is the point of the
fixture: on .NET 8 a signed-out request to the protected JSON endpoint is answered
with a redirect to the sign-in page, and this client relies on that.

Usage:
    python client/democlient.py --base http://127.0.0.1:8080 secret
    python client/democlient.py --base http://127.0.0.1:8080 --login demo demo-password-not-real secret
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    @property
    def location(self) -> str | None:
        return self.headers.get("location")

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


@dataclass
class DemoClient:
    base: str
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)

    def __post_init__(self) -> None:
        self.base = self.base.rstrip("/")
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> Response:
        """One request. Redirects are NOT followed; the caller sees the raw response."""
        url = path if path.startswith("http") else self.base + path
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                return Response(resp.status, _lower(resp.headers), resp.read(), resp.geturl())
        except urllib.error.HTTPError as err:
            return Response(err.code, _lower(err.headers), err.read(), err.geturl())

    def follow(self, resp: Response, max_hops: int = 5) -> Response:
        """Follow redirects the way a browser would, staying on the same origin."""
        hops = 0
        while resp.status in (301, 302, 303, 307, 308) and resp.location and hops < max_hops:
            target = urllib.parse.urljoin(resp.url, resp.location)
            if urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(self.base).netloc:
                raise RuntimeError(f"refusing to follow an off-origin redirect to {target}")
            resp = self.request(target)
            hops += 1
        return resp

    def login(self, username: str, password: str, return_url: str = "/") -> Response:
        return self.request(
            "/login",
            method="POST",
            data={"username": username, "password": password, "returnUrl": return_url},
        )

    def get_secret(self) -> Response:
        # Deliberately no X-Requested-With header: this is a plain navigation, not an XHR.
        return self.request("/api/secret")


def _lower(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--login", nargs=2, metavar=("USER", "PASSWORD"))
    parser.add_argument("action", choices=["secret", "home"])
    args = parser.parse_args(argv)

    client = DemoClient(args.base)
    if args.login:
        resp = client.login(*args.login)
        print(f"POST /login -> {resp.status} location={resp.location!r}")
        if resp.status != 302:
            print(resp.text)
            return 1

    resp = client.get_secret() if args.action == "secret" else client.request("/")
    print(f"first response: {resp.status} content-type={resp.content_type!r} location={resp.location!r}")
    final = client.follow(resp)
    if final is not resp:
        print(f"after redirects: {final.status} {final.url} content-type={final.content_type!r}")
    print(final.text[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
