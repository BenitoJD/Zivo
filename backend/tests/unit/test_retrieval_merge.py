from app.services.retrieval import merge_chunks


def test_merge_chunks_dedupes_preserving_order() -> None:
    a = [
        {"chunk_id": "1", "page_start": 1, "text": "first"},
        {"chunk_id": "2", "page_start": 1, "text": "second"},
    ]
    b = [
        {"chunk_id": "2", "page_start": 1, "text": "dup"},
        {"chunk_id": "3", "page_start": 2, "text": "third"},
    ]
    merged = merge_chunks(a, b)
    assert [c["chunk_id"] for c in merged] == ["1", "2", "3"]
