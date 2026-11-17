"""Shared locations for bundled defaults and editable, persistent policies."""

import os
from pathlib import Path
import tempfile


BUNDLED_POLICIES = Path(__file__).resolve().parent / "policies"


def policy_directory() -> Path:
    configured = os.getenv("POLICY_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else BUNDLED_POLICIES


def initialize_policy_directory() -> None:
    """Seed a new disk atomically without replacing any previously saved edits."""
    directory = policy_directory()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("policy.json", "content.json"):
        if (directory / name).exists():
            continue
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary.write((BUNDLED_POLICIES / name).read_bytes())
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            try:
                os.link(temporary.name, directory / name)
            except FileExistsError:
                pass
        finally:
            Path(temporary.name).unlink()
