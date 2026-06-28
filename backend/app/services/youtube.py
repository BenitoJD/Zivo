"""YouTube → study source: fetch a video's transcript and treat it like an article.

Scribely's headline input is "paste a YouTube link". We turn the video's transcript into
an ImportedArticle that flows through the exact same ingest/index path as URL/text/PDF
sources, so it then works with Learn/Test/Explain/Notes/Cards.

Transcript fetching can be blocked by YouTube from datacenter IPs; failures raise a
WebImportError with a clear message so the caller can tell the user to paste the text.

Reliability note: YouTube hard-blocks the transcript ("timedtext") content fetch from
non-residential IPs (HTTP 429) — verified across youtube-transcript-api, yt-dlp (all
InnerTube clients), and raw fetches. There is no free way around this from a server.
The only reliable route is a residential proxy, so we expose an optional, zero-default
``ZIVO_YOUTUBE_PROXY`` (and ``ZIVO_YOUTUBE_COOKIES_FILE``): set either and transcript
fetching routes through it and starts working — with no behavior change when unset.
"""

from __future__ import annotations
import logging

import os
import re
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.web_import import ImportedArticle, WebImportError

_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
_OEMBED_URL = "https://www.youtube.com/oembed"
_FETCH_TIMEOUT_S = 15.0
_TRANSCRIPT_LANGS = ["en", "en-US", "en-GB"]


logger = logging.getLogger(__name__)

def _transcript_kwargs() -> dict:
    """Optional proxy/cookies for the transcript fetch — the only thing that makes it
    reliable from a server. Empty (no-op) unless ZIVO_YOUTUBE_PROXY / _COOKIES_FILE set."""
    kwargs: dict = {}
    proxy = os.getenv("ZIVO_YOUTUBE_PROXY", "").strip()
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    cookies = os.getenv("ZIVO_YOUTUBE_COOKIES_FILE", "").strip()
    if cookies:
        kwargs["cookies"] = cookies
    return kwargs


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in _YT_HOSTS


def extract_video_id(url: str) -> str | None:
    """Pull the 11-char video id from the common YouTube URL shapes."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate or None
    if host not in _YT_HOSTS:
        return None
    if parsed.path == "/watch":
        vid = parse_qs(parsed.query).get("v", [None])[0]
        if vid:
            return vid
    m = re.match(r"^/(?:shorts|embed|v|live)/([^/?#]+)", parsed.path)
    if m:
        return m.group(1)
    return None


def _fetch_title(video_id: str) -> str:
    try:
        resp = httpx.get(
            _OEMBED_URL,
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=_FETCH_TIMEOUT_S,
        )
        if resp.status_code == 200:
            return str(resp.json().get("title") or "").strip()[:200]
    except Exception:
        logger.debug("youtube oembed title fetch failed", exc_info=True)
    return ""


def _classify_error(exc: Exception) -> WebImportError:
    """Map youtube-transcript-api failures to an honest, actionable message."""
    from youtube_transcript_api import _errors as yt_errors

    if isinstance(exc, (yt_errors.TooManyRequests, yt_errors.YouTubeRequestFailed)) or "429" in str(exc):
        return WebImportError(
            "YouTube is rate-limiting transcript requests right now. Try again in a bit, "
            "or paste the transcript/text instead.",
            code="fetch_failed",
        )
    if isinstance(exc, (yt_errors.TranscriptsDisabled, yt_errors.NoTranscriptFound, yt_errors.NoTranscriptAvailable)):
        return WebImportError(
            "This video has no captions to study. Try another video or paste the text instead.",
            code="empty_content",
        )
    if isinstance(exc, (yt_errors.VideoUnavailable, yt_errors.InvalidVideoId)):
        return WebImportError("That video is unavailable.", code="invalid_url")
    return WebImportError(
        "Couldn't fetch a transcript for this video. Try another video or paste the text instead.",
        code="fetch_failed",
    )


def _fetch_transcript_text(video_id: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise WebImportError(
            "YouTube import is not available right now.", code="fetch_failed"
        ) from exc

    extra = _transcript_kwargs()
    segments = None
    last_exc: Exception | None = None
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=_TRANSCRIPT_LANGS, **extra)
    except Exception as exc:
        last_exc = exc
        # Fall back to any available transcript (manual or auto-generated, any language).
        try:
            listing = YouTubeTranscriptApi.list_transcripts(video_id, **extra)
            for transcript in listing:
                try:
                    segments = transcript.fetch()
                    break
                except Exception as inner:
                    last_exc = inner
                    continue
        except Exception as exc2:
            raise _classify_error(exc2) from exc2

    if not segments:
        raise _classify_error(last_exc or Exception("no segments"))

    parts: list[str] = []
    for seg in segments:
        # Segments are dicts ({"text": ...}) in 0.6.x; objects with .text in newer versions.
        text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
        text = (text or "").replace("\n", " ").strip()
        if text and text != "[Music]":
            parts.append(text)
    joined = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(joined) < 80:
        raise WebImportError(
            "This video's transcript was too short to study. Try another video or paste the text.",
            code="empty_content",
        )
    return joined


def fetch_youtube_transcript(url: str) -> ImportedArticle:
    """Return an ImportedArticle built from a YouTube video's transcript."""
    video_id = extract_video_id(url)
    if not video_id:
        raise WebImportError("That doesn't look like a YouTube video link.", code="invalid_url")

    text = _fetch_transcript_text(video_id)
    title = _fetch_title(video_id) or "YouTube video"
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    return ImportedArticle(
        title=title,
        text=text,
        source_url=canonical,
        source_domain="youtube.com",
    )
