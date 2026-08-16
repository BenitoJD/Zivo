from scripts.dev_cli.cli import main
from scripts.dev_cli.env import pick


def _cli() -> None:
    raise SystemExit(main())


pick(__name__ == "__main__", _cli, lambda: None)
