from pathlib import Path


def repository_sources_dir() -> Path:
    """Return the shared Git-backed source directory regardless of the launch CWD."""
    return Path(__file__).resolve().parent.parent / "sources"
