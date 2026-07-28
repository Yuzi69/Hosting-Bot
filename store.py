import json
import os
from pathlib import Path

DATA_FILE = Path(os.getenv("DATA_FILE", "data/projects.json"))
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
if not DATA_FILE.exists():
    DATA_FILE.write_text("[]")


def _read() -> list:
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception:
        return []


def _write(items: list) -> None:
    DATA_FILE.write_text(json.dumps(items, indent=2))


def all_projects() -> list:
    return _read()


def for_owner(owner_id: int) -> list:
    return [p for p in _read() if p["owner_id"] == owner_id]


def get(project_id: str) -> dict | None:
    return next((p for p in _read() if p["id"] == project_id), None)


def add(project: dict) -> dict:
    items = _read()
    items.append(project)
    _write(items)
    return project


def update(project_id: str, patch: dict) -> dict | None:
    items = _read()
    for i, p in enumerate(items):
        if p["id"] == project_id:
            items[i] = {**p, **patch}
            _write(items)
            return items[i]
    return None


def remove(project_id: str) -> None:
    items = [p for p in _read() if p["id"] != project_id]
    _write(items)


def next_port(base_port: int) -> int:
    used = {p["port"] for p in _read() if p.get("port")}
    port = base_port
    while port in used:
        port += 1
    return port


def slug_taken(slug: str, exclude_id: str | None = None) -> bool:
    return any(p["id"] == slug and p["id"] != exclude_id for p in _read())
