from pathlib import Path


def test_product_http_entrypoint_does_not_exist() -> None:
    main = Path(__file__).resolve().parents[2] / "app" / "main.py"
    assert not main.exists()
