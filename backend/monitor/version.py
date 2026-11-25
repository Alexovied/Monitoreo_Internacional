"""Versión instalada (commit de git) y cuántos cambios hay en GitHub que todavía no tiene.

El commit se lee directo de la carpeta ``.git`` (montada en solo lectura en el
contenedor), sin necesitar el programa git. El mensaje, la fecha y cuántos
cambios faltan los da la API pública de GitHub (``compare``). Sin token, GitHub
admite 60 consultas por hora: la respuesta se guarda ``CACHE_TTL_S``.
"""

import logging
import re
import threading
import time
from pathlib import Path

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"
TIMEOUT_S = 8.0
CACHE_TTL_S = 15 * 60
ERROR_TTL_S = 5 * 60
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_cache = {"key": None, "at": 0.0, "result": None}
_lock = threading.Lock()


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def read_head(git_dir):
    """``{"sha", "branch"}`` del commit actual, o None si no hay repositorio."""
    git_dir = Path(git_dir)
    head = _read(git_dir / "HEAD")
    branch = None
    if head.startswith("ref: "):
        ref = head[5:].strip()
        branch = ref.removeprefix("refs/heads/")
        sha = _read(git_dir / ref) or _packed_ref(git_dir, ref)
    else:
        sha = head  # HEAD suelto (detached): es directamente el commit
    if not SHA_RE.match(sha):
        return None
    return {"sha": sha, "branch": branch}


def _packed_ref(git_dir, ref):
    for line in _read(git_dir / "packed-refs").splitlines():
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return ""


def compare(repo, sha, branch, *, client):
    """Pregunta a GitHub cuántos commits tiene ``branch`` que ``sha`` no."""
    response = client.get(
        f"{API_URL}/repos/{repo}/compare/{sha}...{branch}",
        params={"per_page": 1},
        headers={"Accept": "application/vnd.github+json", "User-Agent": settings.ADSBFI_USER_AGENT},
        timeout=TIMEOUT_S,
    )
    if response.status_code == 404:
        return {"status": "not_found"}
    if response.status_code != 200:
        limited = response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0"
        return {"status": "error",
                "error": "GitHub limitó las consultas; se reintenta en unos minutos." if limited
                else f"GitHub respondió {response.status_code}."}
    data = response.json()
    commit = (data.get("base_commit") or {}).get("commit") or {}
    return {
        "status": "ok",
        "behind": data.get("ahead_by", 0),  # commits de GitHub que acá faltan
        "local_ahead": data.get("behind_by", 0),  # commits de acá que no están en GitHub
        "compare_url": data.get("html_url"),
        "message": (commit.get("message") or "").split("\n", 1)[0],
        "date": (commit.get("committer") or {}).get("date"),
    }


def version_info(*, git_dir=None, repo=None, client=None, clock=time.monotonic):
    git_dir = git_dir or settings.REPO_GIT_DIR
    repo = settings.GITHUB_REPO if repo is None else repo
    head = read_head(git_dir)
    info = {
        "sha": head and head["sha"],
        "branch": head and head["branch"],
        "repo_url": f"https://github.com/{repo}" if repo else None,
        "updates": None,
    }
    if not head or not repo:
        return info

    branch = head["branch"] or "main"
    key = (repo, head["sha"], branch)
    with _lock:
        cached = _cache["result"]
        ttl = ERROR_TTL_S if cached and cached["status"] == "error" else CACHE_TTL_S
        if _cache["key"] != key or cached is None or clock() - _cache["at"] >= ttl:
            try:
                if client is None:
                    with httpx.Client() as own:
                        cached = compare(repo, head["sha"], branch, client=own)
                else:
                    cached = compare(repo, head["sha"], branch, client=client)
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("No se pudo consultar la versión en GitHub: %s", exc.__class__.__name__)
                cached = {"status": "error", "error": "No se pudo conectar con GitHub."}
            _cache.update(key=key, at=clock(), result=cached)
    info["updates"] = cached
    return info


def clear_cache():
    with _lock:
        _cache.update(key=None, at=0.0, result=None)
