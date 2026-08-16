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

from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.services.content_worthiness import is_import_extract_too_short
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
    cookies = os.getenv("ZIVO_YOUTUBE_COOKIES_FILE", "").strip()
    pick(bool(proxy), lambda: kwargs.__setitem__("proxies", {"http": proxy, "https": proxy}), lambda: None)
    pick(bool(cookies), lambda: kwargs.__setitem__("cookies", cookies), lambda: None)
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
    vid = parse_qs(parsed.query).get("v", [None])[0]
    m = re.match(r"^/(?:shorts|embed|v|live)/([^/?#]+)", parsed.path)
    candidate = parsed.path.lstrip("/").split("/")[0]
    rule = first_match(
        [
            Rule(when=(Pred("youtu_be", "truthy"),), action="path"),
            Rule(when=(Pred("unknown_host", "truthy"),), action="none"),
            Rule(when=(Pred("watch", "truthy"), Pred("vid", "truthy")), action="vid"),
            Rule(when=(Pred("m", "truthy"),), action="regex"),
        ],
        {
            "youtu_be": host in {"youtu.be", "www.youtu.be"},
            "unknown_host": host not in _YT_HOSTS,
            "watch": parsed.path == "/watch",
            "vid": bool(vid),
            "m": bool(m),
        },
        default=Rule(when=(), action="none"),
    )
    return apply(
        rule.action,
        {
            "path": lambda: candidate or None,
            "none": lambda: None,
            "vid": lambda: vid,
            "regex": lambda: m.group(1),
        },
    )


def _fetch_title(video_id: str) -> str:
    try:
        resp = httpx.get(
            _OEMBED_URL,
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=_FETCH_TIMEOUT_S,
        )
        return pick(
            resp.status_code == 200,
            lambda: str(resp.json().get("title") or "").strip()[:200],
            lambda: "",
        )
    except Exception:
        logger.debug("youtube oembed title fetch failed", exc_info=True)
    return ""


def _classify_error(exc: Exception) -> WebImportError:
    """Map youtube-transcript-api failures to an honest, actionable message."""
    from youtube_transcript_api import _errors as yt_errors

    rule = first_match(
        [
            Rule(when=(Pred("rate", "truthy"),), action="rate"),
            Rule(when=(Pred("none", "truthy"),), action="none"),
            Rule(when=(Pred("gone", "truthy"),), action="gone"),
        ],
        {
            "rate": isinstance(exc, (yt_errors.TooManyRequests, yt_errors.YouTubeRequestFailed))
            or "429" in str(exc),
            "none": isinstance(
                exc,
                (yt_errors.TranscriptsDisabled, yt_errors.NoTranscriptFound, yt_errors.NoTranscriptAvailable),
            ),
            "gone": isinstance(exc, (yt_errors.VideoUnavailable, yt_errors.InvalidVideoId)),
        },
        default=Rule(when=(), action="generic"),
    )
    return apply(
        rule.action,
        {
            "rate": lambda: WebImportError(
                "YouTube is rate-limiting transcript requests right now. Try again in a bit, "
                "or paste the transcript/text instead.",
                code="fetch_failed",
            ),
            "none": lambda: WebImportError(
                "This video has no captions to study. Try another video or paste the text instead.",
                code="empty_content",
            ),
            "gone": lambda: WebImportError("That video is unavailable.", code="invalid_url"),
            "generic": lambda: WebImportError(
                "Couldn't fetch a transcript for this video. Try another video or paste the text instead.",
                code="fetch_failed",
            ),
        },
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
        try:
            listing = YouTubeTranscriptApi.list_transcripts(video_id, **extra)
            for transcript in listing:
                def _try() -> None:
                    nonlocal segments, last_exc
                    try:
                        segments = transcript.fetch()
                    except Exception as inner:
                        last_exc = inner

                pick(segments is None, _try, lambda: None)
        except Exception as exc2:
            raise _classify_error(exc2) from exc2

    def _no_segments() -> None:
        raise _classify_error(last_exc or Exception("no segments"))

    pick(not segments, _no_segments, lambda: None)

    parts: list[str] = []
    for seg in segments:
        text = pick(
            isinstance(seg, dict),
            lambda: seg.get("text"),
            lambda: getattr(seg, "text", ""),
        )
        text = (text or "").replace("\n", " ").strip()
        pick(bool(text) and text != "[Music]", lambda: parts.append(text), lambda: None)
    joined = re.sub(r"\s+", " ", " ".join(parts)).strip()

    def _too_short() -> None:
        raise WebImportError(
            "This video's transcript was too short to study. Try another video or paste the text.",
            code="empty_content",
        )

    pick(is_import_extract_too_short(joined), _too_short, lambda: None)
    return joined


def fetch_youtube_transcript(url: str) -> ImportedArticle:
    """Return an ImportedArticle built from a YouTube video's transcript."""
    video_id = extract_video_id(url)

    def _bad() -> None:
        raise WebImportError("That doesn't look like a YouTube video link.", code="invalid_url")

    pick(not video_id, _bad, lambda: None)

    text = _fetch_transcript_text(video_id)
    title = _fetch_title(video_id) or "YouTube video"
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    return ImportedArticle(
        title=title,
        text=text,
        source_url=canonical,
        source_domain="youtube.com",
    )
