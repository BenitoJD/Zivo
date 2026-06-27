"""Unit tests for YouTube URL detection/parsing (pure, no network)."""

import pytest

from app.services.youtube import extract_video_id, is_youtube_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=abc123DEFGH&t=10s", "abc123DEFGH"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=xyz", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/XYz_-12345a", "XYz_-12345a"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://example.com/watch?v=nope", None),
        ("https://www.youtube.com/", None),
    ],
)
def test_extract_video_id(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=x", True),
        ("https://youtu.be/x", True),
        ("https://music.youtube.com/watch?v=x", True),
        ("https://example.com/x", False),
        ("https://notyoutube.com.evil.com/x", False),
        ("not a url", False),
    ],
)
def test_is_youtube_url(url, expected):
    assert is_youtube_url(url) is expected
