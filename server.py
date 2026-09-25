"""
Le Monde MCP Server (personal subscriber use)

Tools:
  - list_sections   : Available Le Monde RSS sections
  - get_headlines   : Latest headlines for a section (RSS)
  - search_headlines: Keyword search across recent headlines of several sections
  - get_article     : Full article text, using your subscriber session cookie
  - triage_news     : Rank/classify recent headlines against your interests with Jev (TypeSafe)
"""

import json
import logging
import os
import ssl
import sys
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import keyring
import truststore
from bs4 import BeautifulSoup
from cachetools import TTLCache
from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

# stdout is the MCP stdio channel; log to stderr only.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lemonde_mcp")

BASE_URL = "https://www.lemonde.fr"
KEYCHAIN_SERVICE = "lemonde-mcp"
KEYCHAIN_ACCOUNT = "cookie"
USER_AGENT = os.environ.get(
    "LEMONDE_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
)
CA_BUNDLE = os.environ.get("LEMONDE_CA_BUNDLE", "").strip()
PAYWALL_MARKERS = ("La suite est réservée aux abonnés", "The rest is for subscribers only")

SECTIONS = {
    "une": "/rss/une.xml",
    "en_continu": "/rss/en_continu.xml",
    "international": "/international/rss_full.xml",
    "politique": "/politique/rss_full.xml",
    "societe": "/societe/rss_full.xml",
    "economie": "/economie/rss_full.xml",
    "planete": "/planete/rss_full.xml",
    "sciences": "/sciences/rss_full.xml",
    "pixels": "/pixels/rss_full.xml",
    "culture": "/culture/rss_full.xml",
    "idees": "/idees/rss_full.xml",
    "sport": "/sport/rss_full.xml",
    "en_english": "/en/rss/une.xml",
}

TOPICS = [
    "politics", "international", "economy", "business", "technology", "science",
    "environment", "society", "culture", "sport", "opinion", "other",
]

_feed_cache: TTLCache = TTLCache(maxsize=64, ttl=300)
_article_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)

# Remote mode (Claude web/mobile connector) is enabled when PUBLIC_URL is set.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
ALLOWED_GITHUB_LOGIN = os.environ.get("ALLOWED_GITHUB_LOGIN", "").strip().lower()
CLAUDE_REDIRECT_URIS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]


class OwnerOnly(Middleware):
    # Any GitHub user can complete OAuth; only the owner may use the server.
    async def on_request(self, context: MiddlewareContext, call_next):
        token = get_access_token()
        login = str((token.claims or {}).get("login") or "").lower() if token else ""
        if login != ALLOWED_GITHUB_LOGIN:
            log.warning("Rejected request from GitHub user %r", login)
            raise AuthorizationError("This Le Monde connector is private.")
        return await call_next(context)


def _build_mcp() -> FastMCP:
    if not PUBLIC_URL:
        return FastMCP("lemonde")
    if not ALLOWED_GITHUB_LOGIN:
        raise RuntimeError("ALLOWED_GITHUB_LOGIN must be set in remote mode.")
    auth = GitHubProvider(
        client_id=os.environ["GITHUB_CLIENT_ID"],
        client_secret=os.environ["GITHUB_CLIENT_SECRET"],
        base_url=PUBLIC_URL,
        jwt_signing_key=os.environ.get("JWT_SIGNING_KEY"),
        allowed_client_redirect_uris=CLAUDE_REDIRECT_URIS,
    )
    return FastMCP("lemonde", auth=auth, middleware=[OwnerOnly()])


mcp = _build_mcp()


# ---------------------------------------------------------------------------
# Auth / HTTP
# ---------------------------------------------------------------------------
def _keychain(service: str, account: str) -> str | None:
    try:
        return keyring.get_password(service, account)
    except keyring.errors.KeyringError:
        return None


def _get_cookie() -> str | None:
    return os.environ.get("LEMONDE_COOKIE") or _keychain(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def _ensure_typesafe_key() -> None:
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    value = _keychain("typesafe-shared", "api_key")
    if not value:
        raise ValueError("TypeSafe API key not found. Set TYPESAFE_API_KEY or run scripts/bootstrap_typesafe_key.sh.")
    os.environ["TYPESAFE_API_KEY"] = value


def _validate_lemonde_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "lemonde.fr" or host.endswith(".lemonde.fr")):
        raise ValueError(f"Only https://*.lemonde.fr URLs are allowed, got: {url}")
    return url


def _fetch(url: str, with_cookie: bool) -> httpx.Response:
    # Redirects are followed manually so the subscriber cookie never leaves lemonde.fr.
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}
    cookie = _get_cookie() if with_cookie else None
    if cookie:
        headers["Cookie"] = cookie
    # System trust store picks up the corporate TLS-inspection root CA.
    verify = CA_BUNDLE or truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with httpx.Client(timeout=20.0, follow_redirects=False, verify=verify) as client:
        for _ in range(5):
            _validate_lemonde_url(url)
            resp = client.get(url, headers=headers)
            if resp.is_redirect:
                url = urljoin(url, resp.headers["location"])
                continue
            resp.raise_for_status()
            return resp
    raise RuntimeError("Too many redirects")


def _load_feed(section: str) -> list[dict]:
    if section not in SECTIONS:
        raise ValueError(f"Unknown section '{section}'. Use list_sections().")
    if section in _feed_cache:
        return _feed_cache[section]
    resp = _fetch(BASE_URL + SECTIONS[section], with_cookie=False)
    feed = feedparser.parse(resp.content)
    items = [
        {
            "title": e.get("title", ""),
            "summary": BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True),
            "url": e.get("link", ""),
            "published": e.get("published", ""),
            "section": section,
        }
        for e in feed.entries
    ]
    _feed_cache[section] = items
    return items


def _parse_article(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    meta: dict = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") in ("NewsArticle", "Article", "ReportageNewsArticle"):
                meta = item
                break
        if meta:
            break

    title = soup.select_one("h1.article__title, h1.ds-title")
    desc = soup.select_one("p.article__desc")
    paragraphs = [p.get_text(" ", strip=True) for p in soup.select(".article__paragraph")]
    body = "\n\n".join(p for p in paragraphs if p)
    authors = meta.get("author") or []
    if isinstance(authors, dict):
        authors = [authors]

    return {
        "url": url,
        "title": title.get_text(strip=True) if title else meta.get("headline", ""),
        "description": desc.get_text(" ", strip=True) if desc else meta.get("description", ""),
        "authors": [a.get("name") for a in authors if isinstance(a, dict)],
        "published": meta.get("datePublished", ""),
        "modified": meta.get("dateModified", ""),
        "subscriber_only": meta.get("isAccessibleForFree") in (False, "False", "false"),
        "truncated": any(m in html for m in PAYWALL_MARKERS),
        "text": body,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def list_sections() -> list[str]:
    """List the Le Monde sections available to get_headlines / search_headlines / triage_news."""
    return list(SECTIONS)


@mcp.tool()
def get_headlines(section: str = "une", limit: int = 20) -> list[dict]:
    """Latest Le Monde headlines (title, summary, url, published) for a section. Default: front page ('une')."""
    return _load_feed(section)[: max(1, min(limit, 100))]


@mcp.tool()
def search_headlines(query: str, sections: list[str] | None = None, limit: int = 20) -> list[dict]:
    """Case-insensitive keyword search over recent headlines/summaries across sections (default: all)."""
    terms = [t for t in query.lower().split() if t]
    seen: set[str] = set()
    results = []
    for section in sections or list(SECTIONS):
        for item in _load_feed(section):
            haystack = f"{item['title']} {item['summary']}".lower()
            if item["url"] not in seen and all(t in haystack for t in terms):
                seen.add(item["url"])
                results.append(item)
    return results[: max(1, min(limit, 100))]


@mcp.tool()
def get_article(url: str) -> dict:
    """Fetch the full text of a Le Monde article using your subscriber session.
    'truncated': true means the paywall was hit -> refresh your cookie (scripts/bootstrap_cookie.sh)."""
    _validate_lemonde_url(url)
    if url in _article_cache:
        return _article_cache[url]
    resp = _fetch(url, with_cookie=True)
    article = _parse_article(resp.text, str(resp.url))
    if not article["truncated"]:
        _article_cache[url] = article
    return article


@mcp.tool()
def triage_news(interests: str, sections: list[str] | None = None, limit: int = 15) -> list[dict]:
    """Use Jev (TypeSafe) to score recent headlines for relevance to `interests` (free text, e.g.
    'European AI regulation, French fintech'), flag major stories and assign a topic. Sorted by score."""
    from typesafe_sdk import Choice, Noul, TypeSafeClient

    _ensure_typesafe_key()
    seen: set[str] = set()
    candidates = []
    for section in sections or ["une", "en_continu"]:
        for item in _load_feed(section):
            if item["url"] not in seen:
                seen.add(item["url"])
                candidates.append(item)
    candidates = candidates[: max(1, min(limit, 50))]

    questions = {
        "relevant": Noul(instructions=f"Is this news article relevant to a reader interested in: {interests}?"),
        "major": Noul(instructions="Is this a major, high-impact news story rather than a minor or niche item?"),
        "topic": Choice(instructions="What is the main topic of this article?", criteria={t: None for t in TOPICS}),
    }
    ranked = []
    with TypeSafeClient() as client:
        for item in candidates:
            res = client.system_one(
                state={"title": item["title"], "summary": item["summary"], "section": item["section"]},
                questions=questions,
            )
            relevance = res.nouls["relevant"].noul
            major = res.nouls["major"].noul
            ranked.append({
                **item,
                "relevance": round(relevance, 3),
                "major": round(major, 3),
                "topic": res.choices["topic"].choice,
                "score": round(0.7 * relevance + 0.3 * major, 3),
            })
    return sorted(ranked, key=lambda r: r["score"], reverse=True)


if __name__ == "__main__":
    if PUBLIC_URL:
        mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    else:
        mcp.run()
