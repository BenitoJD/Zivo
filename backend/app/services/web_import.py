"""Fetch public web pages and extract reader-mode article text."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.content_worthiness import (
    is_import_extract_too_short,
    is_section_title_line,
    is_sparse_page_text,
    plan_study_page_split,
    should_break_on_section_heading,
)
from app.services.http_client import HTTP_TIMEOUT_S, zivo_http_client
from app.services.presence import evaluate_presence

READER_PAGE_CHARS = plan_study_page_split().chars_per_page
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_S = HTTP_TIMEOUT_S
MAX_REDIRECTS = 3
_METADATA_IP = ipaddress.ip_address("169.254.169.254")

_WS_RE = re.compile(r"[ \t]+\n")
_BLANK_RE = re.compile(r"\n{3,}")
# Soft page breaks at structural headings so DOCX/paste/URL study units track
# document outline (Part / Chapter / markdown #) instead of only char budgets.
_SECTION_START_RE = re.compile(
    r"^(?:"
    r"#{1,6}\s+\S"  # markdown heading
    r"|(?:Part|Chapter|Section|Unit|Module)\s+\d+"
    r")",
    re.IGNORECASE,
)

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg"})
_BLOCK_TAGS = frozenset({"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li"})
_FLOW_TAGS = _BLOCK_TAGS | {"br"}
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)
_HTTP_RULES = (
    Rule(when=(Pred("redirect", "truthy"),), action="redirect"),
    Rule(when=(Pred("error", "truthy"),), action="error"),
    Rule(when=(), action="ok"),
)
_URL_RULES = (
    Rule(when=(Pred("blank", "truthy"),), action="enter"),
    Rule(when=(Pred("scheme_ok", "falsey"),), action="scheme"),
    Rule(when=(Pred("host", "falsey"),), action="host"),
    Rule(when=(Pred("creds", "truthy"),), action="creds"),
    Rule(when=(Pred("bad_port", "truthy"),), action="port"),
    Rule(when=(), action="ok"),
)


def _raise(exc: BaseException) -> None:
    raise exc


def _raise_from(cause: BaseException, wrapped: BaseException) -> None:
    raise wrapped from cause


def _looks_like_section_start(paragraph: str) -> bool:
    stripped = paragraph.strip()
    first = stripped.split("\n", 1)[0].strip()
    return bool(first) and bool(_SECTION_START_RE.match(first)) and is_section_title_line(stripped)


@dataclass(frozen=True)
class ImportedArticle:
    title: str
    text: str
    source_url: str
    source_domain: str


class WebImportError(Exception):
    def __init__(self, message: str, *, code: str = "import_failed") -> None:
        super().__init__(message)
        self.code = code


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._blocks: list[str] = []
        self._buffer: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False
        self._og_title: str | None = None
        self._h1: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}

        def _enter_skip() -> None:
            self._skip_depth += 1

        def _process() -> None:
            pick(tag == "title", lambda: setattr(self, "_in_title", True), lambda: None)
            pick(
                tag == "meta"
                and attr_map.get("property") == "og:title"
                and bool(attr_map.get("content")),
                lambda: setattr(self, "_og_title", attr_map["content"].strip()),
                lambda: None,
            )
            pick(tag in _FLOW_TAGS, lambda: self._on_flow_start(tag), lambda: None)

        apply(
            first_match(
                (
                    Rule(when=(Pred("skip_tag", "truthy"),), action="enter_skip"),
                    Rule(when=(Pred("skipping", "truthy"),), action="noop"),
                    Rule(when=(), action="process"),
                ),
                {"skip_tag": tag in _SKIP_TAGS, "skipping": bool(self._skip_depth)},
            ).action,
            {"enter_skip": _enter_skip, "noop": lambda: None, "process": _process},
        )

    def _on_flow_start(self, tag: str) -> None:
        pick(tag == "h1" and not self._h1, lambda: setattr(self, "_buffer", []), lambda: None)
        pick(tag in _BLOCK_TAGS and bool(self._buffer), self._flush_block, lambda: None)
        pick(tag == "br", lambda: self._buffer.append("\n"), lambda: None)

    def handle_endtag(self, tag: str) -> None:
        def _leave_skip() -> None:
            self._skip_depth = max(0, self._skip_depth - 1)

        def _process() -> None:
            pick(tag == "title", lambda: setattr(self, "_in_title", False), lambda: None)
            pick(tag in _BLOCK_TAGS, lambda: self._on_block_end(tag), lambda: None)

        apply(
            first_match(
                (
                    Rule(when=(Pred("skip_tag", "truthy"),), action="leave_skip"),
                    Rule(when=(Pred("skipping", "truthy"),), action="noop"),
                    Rule(when=(), action="process"),
                ),
                {"skip_tag": tag in _SKIP_TAGS, "skipping": bool(self._skip_depth)},
            ).action,
            {"leave_skip": _leave_skip, "noop": lambda: None, "process": _process},
        )

    def _on_block_end(self, tag: str) -> None:
        self._flush_block()
        pick(
            tag == "h1" and bool(self._blocks),
            self._maybe_h1,
            lambda: None,
        )

    def _maybe_h1(self) -> None:
        candidate = self._blocks[-1].strip()
        pick(
            bool(candidate) and not self._h1,
            lambda: setattr(self, "_h1", candidate),
            lambda: None,
        )

    def handle_data(self, data: str) -> None:
        def _visible() -> None:
            pick(
                self._in_title,
                lambda: self._title_parts.append(data),
                lambda: pick(
                    bool(data.strip()),
                    lambda: self._buffer.append(data),
                    lambda: None,
                ),
            )

        pick(bool(self._skip_depth), lambda: None, _visible)

    def _flush_block(self) -> None:
        block = "".join(self._buffer).strip()
        self._buffer = []
        pick(
            not block,
            lambda: None,
            lambda: self._blocks.append(re.sub(r"\s+", " ", block)),
        )

    def result(self) -> tuple[str, str]:
        self._flush_block()
        title = (self._og_title or " ".join(self._title_parts).strip() or self._h1 or "Web article").strip()
        text = "\n\n".join(self._blocks).strip()
        return title, text


def normalize_public_url(raw: str) -> str:
    value = (raw or "").strip()
    value = pick(
        bool(re.match(r"^https?://", value, flags=re.IGNORECASE)),
        lambda: value,
        lambda: f"https://{value}",
    )
    parsed = urlparse(value)
    apply(
        first_match(
            _URL_RULES,
            {
                "blank": not (raw or "").strip(),
                "scheme_ok": parsed.scheme in {"http", "https"},
                "host": bool(parsed.hostname),
                "creds": bool(parsed.username or parsed.password),
                "bad_port": bool(parsed.port) and parsed.port not in {80, 443},
            },
        ).action,
        {
            "enter": lambda: _raise(WebImportError("Enter a link to import", code="invalid_url")),
            "scheme": lambda: _raise(
                WebImportError("Only http and https links are supported", code="invalid_url")
            ),
            "host": lambda: _raise(WebImportError("Enter a valid link", code="invalid_url")),
            "creds": lambda: _raise(
                WebImportError("Links with embedded credentials are not supported", code="invalid_url")
            ),
            "port": lambda: _raise(
                WebImportError("Only standard web ports are supported", code="invalid_url")
            ),
            "ok": lambda: None,
        },
    )
    _assert_public_host(parsed.hostname)
    return parsed.geturl()


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip == _METADATA_IP
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or pick(
            isinstance(ip, ipaddress.IPv4Address),
            lambda: ip in ipaddress.ip_network("10.0.0.0/8")
            or ip in ipaddress.ip_network("172.16.0.0/12")
            or ip in ipaddress.ip_network("192.168.0.0/16"),
            lambda: False,
        )
    )


def _assert_public_host(hostname: str) -> None:
    host = hostname.lower().rstrip(".")
    pick(
        host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"),
        lambda: _raise(WebImportError("That link points to a private address", code="blocked_url")),
        lambda: None,
    )
    try:
        as_ip = ipaddress.ip_address(host)
    except ValueError:
        as_ip = None
    pick(
        as_ip is not None and _is_blocked_ip(as_ip),
        lambda: _raise(WebImportError("That link points to a private address", code="blocked_url")),
        lambda: None,
    )
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        _raise_from(exc, WebImportError("Could not resolve that link", code="fetch_failed"))
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        pick(
            _is_blocked_ip(ip),
            lambda: _raise(WebImportError("That link points to a private address", code="blocked_url")),
            lambda: None,
        )


def paginate_reader_text(text: str, chars_per_page: int | None = None) -> list[dict]:
    split = plan_study_page_split()
    chars_per_page = pick(
        chars_per_page is None,
        lambda: split.chars_per_page,
        lambda: int(chars_per_page),
    )
    cleaned = _BLANK_RE.sub("\n\n", _WS_RE.sub("\n", text.strip()))

    def _paginate() -> list[dict]:
        paragraphs = list(filter(None, map(str.strip, cleaned.split("\n\n"))))
        pages: list[dict] = []
        current: list[str] = []
        current_len = 0
        page_num = 1

        def flush() -> None:
            nonlocal page_num, current, current_len
            pick(
                not current,
                lambda: None,
                lambda: pages.append({"page": page_num, "text": "\n\n".join(current)}),
            )
            page_num += choose(bool(current), 1, 0)
            current = []
            current_len = 0

        def _emit_long(para: str) -> None:
            nonlocal page_num
            flush()
            start = 0
            while start < len(para):
                end = min(len(para), start + chars_per_page)
                pages.append({"page": page_num, "text": para[start:end].strip()})
                page_num += 1
                start = end

        def _emit_short(para: str) -> None:
            nonlocal current_len
            add_len = len(para) + choose(bool(current), 2, 0)
            pick(
                bool(current) and current_len + add_len > chars_per_page,
                flush,
                lambda: None,
            )
            current.append(para)
            current_len += add_len

        for para in paragraphs:
            pick(
                should_break_on_section_heading(
                    current_len=current_len,
                    looks_like_heading=bool(current) and _looks_like_section_start(para),
                ),
                flush,
                lambda: None,
            )
            pick(
                len(para) > chars_per_page,
                lambda p=para: _emit_long(p),
                lambda p=para: _emit_short(p),
            )

        flush()
        return pages or [{"page": 1, "text": cleaned}]

    return pick(not cleaned, lambda: [{"page": 1, "text": ""}], _paginate)


def encode_article_pages(pages: list[dict]) -> bytes:
    return json.dumps({"pages": pages}, ensure_ascii=False).encode("utf-8")


def article_filename(title: str, domain: str) -> str:
    base = re.sub(r"[^\w\s-]", "", title).strip() or domain
    base = re.sub(r"\s+", " ", base)[:80].strip() or domain
    return f"{base}.article"


def source_domain(url: str) -> str:
    host = urlparse(url).hostname or "web"
    return choose(host.startswith("www."), host[4:], host)


def _clean_text(text: str) -> str:
    text = _BLANK_RE.sub("\n\n", _WS_RE.sub("\n", text.strip()))
    return text.strip()


def extract_article(content: bytes, content_type: str, url: str) -> ImportedArticle:
    ct = (content_type or "").split(";")[0].strip().lower()
    domain = source_domain(url)

    def _plain() -> ImportedArticle:
        text = _clean_text(content.decode("utf-8", errors="replace"))
        pick(
            is_import_extract_too_short(text),
            lambda: _raise(
                WebImportError("This page did not contain enough text to study", code="empty_content")
            ),
            lambda: None,
        )
        return ImportedArticle(title=domain, text=text, source_url=url, source_domain=domain)

    def _html() -> ImportedArticle:
        html = content.decode("utf-8", errors="replace")
        parser = _TextExtractor()
        parser.feed(html)
        parser.close()
        title, text = parser.result()
        text = _clean_text(text)
        pick(
            is_import_extract_too_short(text),
            lambda: _raise(
                WebImportError(
                    "We couldn't extract readable article text from this page. Try pasting the article instead.",
                    code="empty_content",
                )
            ),
            lambda: None,
        )
        return ImportedArticle(
            title=title[:200] or domain,
            text=text,
            source_url=url,
            source_domain=domain,
        )

    return pick(ct.startswith("text/plain"), _plain, _html)


async def fetch_and_extract(url: str) -> ImportedArticle:
    normalized = normalize_public_url(url)
    headers = {"Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8"}
    state: dict[str, Any] = {
        "url": normalized,
        "body": None,
        "ct": None,
        "final": None,
        "done": False,
    }

    async def _noop() -> None:
        return None

    async def _one_round(client: httpx.AsyncClient, redirect_count: int) -> None:
        async with client.stream("GET", state["url"]) as response:

            async def _redir() -> None:
                pick(
                    redirect_count >= MAX_REDIRECTS,
                    lambda: _raise(
                        WebImportError(
                            "Too many redirects while fetching this page",
                            code="fetch_failed",
                        )
                    ),
                    lambda: None,
                )
                location = response.headers.get("location")
                apply(
                    evaluate_presence(location).action,
                    {
                        "missing": lambda: _raise(
                            WebImportError(
                                "This page could not be fetched. Try pasting the article instead.",
                                code="fetch_failed",
                            )
                        ),
                        "empty": lambda: _raise(
                            WebImportError(
                                "This page could not be fetched. Try pasting the article instead.",
                                code="fetch_failed",
                            )
                        ),
                        "ok": lambda: None,
                    },
                )
                await response.aread()
                state["url"] = normalize_public_url(urljoin(state["url"], location))

            async def _err() -> None:
                raise WebImportError(
                    "This page could not be fetched. Try pasting the article instead.",
                    code="fetch_failed",
                )

            async def _ok() -> None:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    pick(
                        total > MAX_RESPONSE_BYTES,
                        lambda: _raise(
                            WebImportError("This page is too large to import", code="too_large")
                        ),
                        lambda: None,
                    )
                    chunks.append(chunk)
                state["body"] = b"".join(chunks)
                state["ct"] = response.headers.get("content-type", "text/html")
                state["final"] = state["url"]
                state["done"] = True

            hit = first_match(
                _HTTP_RULES,
                {
                    "redirect": response.status_code in {301, 302, 303, 307, 308},
                    "error": response.status_code >= 400,
                },
            )
            await apply(
                hit.action,
                {"redirect": _redir, "error": _err, "ok": _ok},
            )

    try:
        async with zivo_http_client(
            follow_redirects=False,
            timeout=FETCH_TIMEOUT_S,
            headers=headers,
        ) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                await pick(
                    state["done"],
                    _noop,
                    lambda n=redirect_count: _one_round(client, n),
                )
    except WebImportError:
        raise
    except httpx.HTTPError as exc:
        _raise_from(
            exc,
            WebImportError(
                "This page could not be fetched. Try pasting the article instead.",
                code="fetch_failed",
            ),
        )

    pick(
        state["done"],
        lambda: None,
        lambda: _raise(
            WebImportError(
                "This page could not be fetched. Try pasting the article instead.",
                code="fetch_failed",
            )
        ),
    )
    article = extract_article(state["body"], state["ct"], state["final"])
    return ImportedArticle(
        title=article.title,
        text=article.text,
        source_url=state["final"],
        source_domain=source_domain(state["final"]),
    )


def article_from_pasted_text(
    text: str, *, title: str | None = None, source_url: str | None = None
) -> ImportedArticle:
    cleaned = _clean_text(text)
    pick(
        is_sparse_page_text(cleaned),
        lambda: _raise(WebImportError("Add a bit more text to study from", code="empty_content")),
        lambda: None,
    )
    url = apply(
        evaluate_presence((source_url or "").strip()).action,
        {
            "ok": lambda: normalize_public_url(source_url),
            "empty": lambda: "",
            "missing": lambda: "",
        },
    )
    domain = choose(bool(url), source_domain(url), "pasted")
    resolved_title = (title or "").strip() or choose(bool(url), source_domain(url), "Pasted article")
    return ImportedArticle(
        title=resolved_title[:200],
        text=cleaned,
        source_url=url,
        source_domain=domain,
    )


def build_document_meta(article: ImportedArticle, *, source_type: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source_type": source_type,
        "page_title": article.title,
    }
    pick(
        bool(article.source_url),
        lambda: meta.update({"source_url": article.source_url, "source_domain": article.source_domain}),
        lambda: None,
    )
    return meta
