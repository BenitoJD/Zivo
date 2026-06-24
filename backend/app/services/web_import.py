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

READER_PAGE_CHARS = 3200
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_S = 15.0
MAX_REDIRECTS = 3
USER_AGENT = "zivo/1.0 (+https://zivo.dev; article import)"
_METADATA_IP = ipaddress.ip_address("169.254.169.254")

_WS_RE = re.compile(r"[ \t]+\n")
_BLANK_RE = re.compile(r"\n{3,}")


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
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta" and attr_map.get("property") == "og:title" and attr_map.get("content"):
            self._og_title = attr_map["content"].strip()
        if tag in {"p", "br", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li"}:
            if tag == "h1" and not self._h1:
                self._buffer = []
            if tag in {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li"} and self._buffer:
                self._flush_block()
            if tag == "br":
                self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li"}:
            self._flush_block()
            if tag == "h1" and self._blocks:
                candidate = self._blocks[-1].strip()
                if candidate and not self._h1:
                    self._h1 = candidate

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        text = data.strip()
        if text:
            self._buffer.append(data)

    def _flush_block(self) -> None:
        block = "".join(self._buffer).strip()
        self._buffer = []
        if not block:
            return
        block = re.sub(r"\s+", " ", block)
        self._blocks.append(block)

    def result(self) -> tuple[str, str]:
        self._flush_block()
        title = (self._og_title or " ".join(self._title_parts).strip() or self._h1 or "Web article").strip()
        text = "\n\n".join(self._blocks).strip()
        return title, text


def normalize_public_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise WebImportError("Enter a link to import", code="invalid_url")
    if not re.match(r"^https?://", value, flags=re.IGNORECASE):
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise WebImportError("Only http and https links are supported", code="invalid_url")
    if not parsed.hostname:
        raise WebImportError("Enter a valid link", code="invalid_url")
    if parsed.username or parsed.password:
        raise WebImportError("Links with embedded credentials are not supported", code="invalid_url")
    _assert_public_host(parsed.hostname)
    port = parsed.port
    if port and port not in {80, 443}:
        raise WebImportError("Only standard web ports are supported", code="invalid_url")
    return parsed.geturl()


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip == _METADATA_IP:
        return True
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return ip in ipaddress.ip_network("10.0.0.0/8") or ip in ipaddress.ip_network(
            "172.16.0.0/12"
        ) or ip in ipaddress.ip_network("192.168.0.0/16")
    return False


def _assert_public_host(hostname: str) -> None:
    host = hostname.lower().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1", "169.254.169.254"} or host.endswith(".local"):
        raise WebImportError("That link points to a private address", code="blocked_url")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebImportError("Could not resolve that link", code="fetch_failed") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise WebImportError("That link points to a private address", code="blocked_url")


def paginate_reader_text(text: str, chars_per_page: int = READER_PAGE_CHARS) -> list[dict]:
    cleaned = _BLANK_RE.sub("\n\n", _WS_RE.sub("\n", text.strip()))
    if not cleaned:
        return [{"page": 1, "text": ""}]

    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    pages: list[dict] = []
    current: list[str] = []
    current_len = 0
    page_num = 1

    def flush() -> None:
        nonlocal page_num, current, current_len
        if not current:
            return
        pages.append({"page": page_num, "text": "\n\n".join(current)})
        page_num += 1
        current = []
        current_len = 0

    for para in paragraphs:
        if len(para) > chars_per_page:
            flush()
            start = 0
            while start < len(para):
                end = min(len(para), start + chars_per_page)
                pages.append({"page": page_num, "text": para[start:end].strip()})
                page_num += 1
                start = end
            continue

        add_len = len(para) + (2 if current else 0)
        if current and current_len + add_len > chars_per_page:
            flush()
        current.append(para)
        current_len += add_len

    flush()
    return pages or [{"page": 1, "text": cleaned}]


def encode_article_pages(pages: list[dict]) -> bytes:
    return json.dumps({"pages": pages}, ensure_ascii=False).encode("utf-8")


def article_filename(title: str, domain: str) -> str:
    base = re.sub(r"[^\w\s-]", "", title).strip() or domain
    base = re.sub(r"\s+", " ", base)[:80].strip() or domain
    return f"{base}.article"


def source_domain(url: str) -> str:
    host = urlparse(url).hostname or "web"
    return host[4:] if host.startswith("www.") else host


def _clean_text(text: str) -> str:
    text = _BLANK_RE.sub("\n\n", _WS_RE.sub("\n", text.strip()))
    return text.strip()


def extract_article(content: bytes, content_type: str, url: str) -> ImportedArticle:
    ct = (content_type or "").split(";")[0].strip().lower()
    domain = source_domain(url)

    if ct.startswith("text/plain"):
        text = _clean_text(content.decode("utf-8", errors="replace"))
        if len(text) < 80:
            raise WebImportError("This page did not contain enough text to study", code="empty_content")
        return ImportedArticle(title=domain, text=text, source_url=url, source_domain=domain)

    html = content.decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    title, text = parser.result()
    text = _clean_text(text)
    if len(text) < 80:
        raise WebImportError(
            "We couldn't extract readable article text from this page. Try pasting the article instead.",
            code="empty_content",
        )
    return ImportedArticle(
        title=title[:200] or domain,
        text=text,
        source_url=url,
        source_domain=domain,
    )


async def fetch_and_extract(url: str) -> ImportedArticle:
    normalized = normalize_public_url(url)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8"}

    current_url = normalized
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=FETCH_TIMEOUT_S,
            headers=headers,
        ) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= MAX_REDIRECTS:
                            raise WebImportError(
                                "Too many redirects while fetching this page",
                                code="fetch_failed",
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise WebImportError(
                                "This page could not be fetched. Try pasting the article instead.",
                                code="fetch_failed",
                            )
                        await response.aread()
                        current_url = normalize_public_url(urljoin(current_url, location))
                        continue

                    if response.status_code >= 400:
                        raise WebImportError(
                            "This page could not be fetched. Try pasting the article instead.",
                            code="fetch_failed",
                        )
                    final_url = current_url
                    content_type = response.headers.get("content-type", "text/html")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise WebImportError("This page is too large to import", code="too_large")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    break
            else:
                raise WebImportError(
                    "This page could not be fetched. Try pasting the article instead.",
                    code="fetch_failed",
                )
    except WebImportError:
        raise
    except httpx.HTTPError as exc:
        raise WebImportError(
            "This page could not be fetched. Try pasting the article instead.",
            code="fetch_failed",
        ) from exc

    article = extract_article(body, content_type, final_url)
    return ImportedArticle(
        title=article.title,
        text=article.text,
        source_url=final_url,
        source_domain=source_domain(final_url),
    )


def article_from_pasted_text(text: str, *, title: str | None = None, source_url: str | None = None) -> ImportedArticle:
    cleaned = _clean_text(text)
    if len(cleaned) < 40:
        raise WebImportError("Add a bit more text to study from", code="empty_content")
    url = normalize_public_url(source_url) if source_url and source_url.strip() else ""
    domain = source_domain(url) if url else "pasted"
    resolved_title = (title or "").strip() or (source_domain(url) if url else "Pasted article")
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
    if article.source_url:
        meta["source_url"] = article.source_url
        meta["source_domain"] = article.source_domain
    return meta
