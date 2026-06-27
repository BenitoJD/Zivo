"""Unit tests for the Memory Palace parser/normalizer (pure, no DB/network)."""

from app.graphs.memory_palace_graph import _MAX_STATIONS, _finalize, _parse_palace, _slugify


def _station(term: str, **over) -> dict:
    base = {"locus": "the door", "term": term, "fact": f"{term} fact.", "image": f"a vivid {term} scene", "cue": "what?"}
    base.update(over)
    return base


def test_slugify_unique_and_safe():
    taken: set[str] = set()
    assert _slugify("Surface Water!", taken) == "surface-water"
    assert _slugify("Surface Water!", taken) == "surface-water-2"
    assert _slugify("", taken) == "station"


def test_parse_palace_plain_object():
    raw = '{"setting": "Kitchen", "intro": "Walk in.", "stations": [{"locus":"tap","term":"X","fact":"f","image":"i","cue":"c"}]}'
    data = _parse_palace(raw)
    assert data["setting"] == "Kitchen" and len(data["stations"]) == 1


def test_parse_palace_strips_fence_and_prose():
    raw = 'Sure! Here:\n```json\n{"setting":"Home","stations":[]}\n```\nEnjoy.'
    assert _parse_palace(raw)["setting"] == "Home"


def test_parse_palace_garbage():
    assert _parse_palace("") == {}
    assert _parse_palace("no json here") == {}


def test_finalize_requires_min_stations():
    data = {"setting": "Home", "stations": [_station(f"t{i}") for i in range(3)]}
    assert _finalize(data, "fallback") == {}  # below _MIN_STATIONS


def test_finalize_normalizes_caps_and_keys():
    data = {
        "setting": "",
        "stations": [_station(f"term{i}") for i in range(_MAX_STATIONS + 5)],
    }
    out = _finalize(data, "My House")
    assert out["setting"] == "My House"  # falls back when model omits setting
    assert len(out["stations"]) == _MAX_STATIONS
    keys = [s["key"] for s in out["stations"]]
    assert len(set(keys)) == len(keys)  # unique slugs
    assert all({"key", "locus", "term", "fact", "image", "cue"} <= s.keys() for s in out["stations"])


def test_finalize_drops_incomplete_stations():
    data = {
        "setting": "Home",
        "stations": [
            _station("Good"),
            {"locus": "x", "term": "NoImage", "fact": "f", "cue": "c"},  # missing image → dropped
            _station("Also good"),
            _station("Third"),
            _station("Fourth"),
        ],
    }
    out = _finalize(data, "Home")
    terms = [s["term"] for s in out["stations"]]
    assert "NoImage" not in terms and len(out["stations"]) == 4
