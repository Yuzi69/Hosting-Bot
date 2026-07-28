import os
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

import store

HOSTED_DIR = Path(os.getenv("HOSTED_APPS_DIR", "hosted"))
BASE_PORT = int(os.getenv("BASE_PORT", "4000"))
HOSTED_DIR.mkdir(parents=True, exist_ok=True)


class DeployError(Exception):
    pass


def sanitize_slug(raw: str | None) -> str | None:
    """Turn a user-supplied caption into a URL-safe subdomain/path slug."""
    if not raw:
        return None
    slug = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug[:30]
    return slug or None


def resolve_project_id(slug: str | None, existing_id: str | None) -> str:
    if existing_id:
        return existing_id
    if slug and not store.slug_taken(slug):
        return slug
    if slug:
        return f"{slug}-{uuid.uuid4().hex[:4]}"
    return uuid.uuid4().hex[:8]


def _run(cmd: str, cwd: Path, timeout: int = 300, extra_env: dict | None = None) -> str:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )
    if result.returncode != 0:
        raise DeployError(f"`{cmd}` failed:\n{result.stderr[-800:]}")
    return result.stdout


def _find_project_root(directory: Path) -> Path:
    """Unwrap a single top-level folder (common in GitHub-style zip exports)."""
    entries = [e for e in directory.iterdir() if e.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return _find_project_root(entries[0])
    return directory


def _detect_type(root: Path) -> str:
    if (root / "package.json").exists():
        return "node"
    if (root / "pom.xml").exists():
        return "java-maven"
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return "java-gradle"
    return "static"


def _pick_node_entry(root: Path, pkg: dict) -> str | None:
    if pkg.get("scripts", {}).get("start"):
        return None  # use `npm start`
    for candidate in ("server.js", "index.js", "app.js", "main.js"):
        if (root / candidate).exists():
            return candidate
    return "index.js"


def _find_jar(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    for f in directory.iterdir():
        if f.suffix == ".jar" and not f.name.endswith("-sources.jar"):
            return f
    return None


def _stop_pm2(project_id: str) -> None:
    subprocess.run(["pm2", "delete", project_id], capture_output=True)


def deploy_zip(
    zip_path: str,
    project_name: str,
    owner_id: int,
    slug: str | None = None,
    domain: str | None = None,
    existing_id: str | None = None,
) -> dict:
    project_id = resolve_project_id(slug, existing_id)
    dest = HOSTED_DIR / project_id

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)

    root = _find_project_root(dest)
    ptype = _detect_type(root)

    record = store.get(project_id) or {
        "id": project_id,
        "name": project_name,
        "owner_id": owner_id,
        "port": None,
    }
    record.update({"type": ptype, "path": str(root), "status": "deploying"})
    if domain:
        record["domain"] = domain
    record = store.update(project_id, record) or store.add(record)

    try:
        if ptype == "static":
            record = store.update(project_id, {"status": "running"})

        elif ptype == "node":
            import json
            pkg = json.loads((root / "package.json").read_text())
            _run("npm install --omit=dev --no-audit --no-fund", root)
            entry = _pick_node_entry(root, pkg)
            port = record["port"] or store.next_port(BASE_PORT)

            _stop_pm2(project_id)
            if entry:
                cmd = f'pm2 start "{entry}" --name "{project_id}" --cwd "{root}"'
            else:
                cmd = f'pm2 start npm --name "{project_id}" --cwd "{root}" -- start'
            _run(cmd, root, extra_env={"PORT": str(port)})
            record = store.update(project_id, {"port": port, "status": "running"})

        elif ptype in ("java-maven", "java-gradle"):
            if ptype == "java-maven":
                _run("mvn -q -DskipTests package", root, timeout=600)
                jar = _find_jar(root / "target")
            else:
                gradlew = root / "gradlew"
                runner = "./gradlew" if gradlew.exists() else "gradle"
                _run(f"chmod +x gradlew 2>/dev/null; {runner} build -x test", root, timeout=600)
                jar = _find_jar(root / "build" / "libs")

            if not jar:
                raise DeployError("Build succeeded but no runnable .jar was found.")

            port = record["port"] or store.next_port(BASE_PORT)
            _stop_pm2(project_id)
            _run(
                f'pm2 start "java -jar {jar} --server.port={port}" --name "{project_id}" --cwd "{root}"',
                root,
            )
            record = store.update(project_id, {"port": port, "status": "running", "jar": str(jar)})

    except DeployError:
        store.update(project_id, {"status": "failed"})
        raise
    except Exception as e:
        store.update(project_id, {"status": "failed"})
        raise DeployError(str(e))

    return record


def deploy_single_html(
    file_path: str,
    project_name: str,
    owner_id: int,
    slug: str | None = None,
    domain: str | None = None,
    existing_id: str | None = None,
) -> dict:
    """Host a single uploaded .html file directly, no zip needed."""
    project_id = resolve_project_id(slug, existing_id)
    dest = HOSTED_DIR / project_id

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy(file_path, dest / "index.html")

    record = store.get(project_id) or {
        "id": project_id,
        "name": project_name,
        "owner_id": owner_id,
        "port": None,
    }
    record.update({"type": "static", "path": str(dest), "status": "running"})
    if domain:
        record["domain"] = domain
    record = store.update(project_id, record) or store.add(record)
    return record


def stop_project(project_id: str) -> None:
    _stop_pm2(project_id)
    store.update(project_id, {"status": "stopped"})


def delete_project(project_id: str) -> None:
    project = store.get(project_id)
    _stop_pm2(project_id)
    if project:
        d = HOSTED_DIR / project_id
        if d.exists():
            shutil.rmtree(d)
    store.remove(project_id)
