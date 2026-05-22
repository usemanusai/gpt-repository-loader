"""Entry point used by ``python -m gpt_repository_loader``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
