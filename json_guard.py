"""Fail-closed guard for production JSON records and unresolved Git conflicts.

Used before publishing repository state, never uploads media or changes records.
"""
import argparse
import json
from pathlib import Path

# Optional data files are validated if present. Missing optional queue is not
# a reason to invent records or block a fresh installation.
JSON_FILES = {
    "history.json": list,
    "ig_queue.json": list,
    "related_video_tasks.json": list,
    "data/golgedefter/history.json": list,
    "data/golgedefter/ig_queue.json": list,
    "data/golgedefter/related_video_tasks.json": list,
    "watchdog_state.json": dict,
}
MARKERS = ("<<<<<<< ", "\n=======", "\n>>>>>>> ")


def validate_text(text, label="JSON", shape=None):
    if any(marker in text for marker in MARKERS):
        raise ValueError(f"{label}: unresolved Git merge conflict; refusing to overwrite records")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: invalid JSON line {exc.lineno} column {exc.colno}; refusing to overwrite") from None
    if shape is not None and not isinstance(data, shape):
        raise ValueError(f"{label}: unexpected JSON root type; refusing to overwrite")
    return data


def validate_repo(root):
    """Read only. Validate all known persistent JSON files that exist."""
    root = Path(root)
    checked = []
    for relative, shape in JSON_FILES.items():
        path = root / relative
        if path.exists():
            validate_text(path.read_text(encoding="utf-8"), relative, shape)
            checked.append(relative)
    return checked


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    result = validate_repo(args.root)
    print(f"OK: {len(result)} persistent JSON records valid; no unresolved Git conflicts")


if __name__ == "__main__":
    main()
