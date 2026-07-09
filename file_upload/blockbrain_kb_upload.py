"""
Standalone Blockbrain knowledge-base + folder + file-upload helper.

Extracted from the Blockbrain upload / KB-management cells of
jira-BB-SupplyOn_adapter.ipynb so this logic can be reused outside a
notebook: create (or resolve) a knowledge base, mirror a local directory
tree into matching Blockbrain folders, and upload files into them.

Usage:
    python blockbrain_kb_upload.py --source DB_content_jira/CORPIT --kb-name atlassian-jira

Configuration is read from a dedicated `.env.upload` file (next to this
script, NOT the project's shared `.env`) loaded automatically:
    BB_API_URL              e.g. https://blocky.theblockbrain.ai/files/v2
    BB_API_KEY              Blockbrain API key
    BB_EMBEDDING_MODEL      optional, defaults to "azure-emb-3-large" (used
                            only when a new KB is created)
    BB_ENABLE_EXTRACT_IMAGE optional, "true"/"false", defaults to "true"
    BB_PARENT_PATH          optional, defaults to "root"
    UPLOAD_SLEEP            optional, seconds between uploads, defaults to 0.1
    BB_KB_SLUG_CACHE_FILE   optional, defaults to "utils/bb_kb_slugs.json"

See `.env.upload.example` for a template.
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env.upload")

BB_API_URL = os.environ["BB_API_URL"]
BB_API_KEY = os.environ["BB_API_KEY"]
BB_PARENT_PATH = os.environ.get("BB_PARENT_PATH", "root")
UPLOAD_SLEEP = float(os.environ.get("UPLOAD_SLEEP", "0.1"))
BB_EMBEDDING_MODEL = os.environ.get("BB_EMBEDDING_MODEL", "azure-emb-3-large")
BB_ENABLE_EXTRACT_IMAGE = os.environ.get("BB_ENABLE_EXTRACT_IMAGE", "true").lower() == "true"

BB_KB_URL = BB_API_URL.rsplit("/files/v2", 1)[0] + "/knowledge_base"
BB_FOLDER_URL = BB_API_URL.rsplit("/files/v2", 1)[0] + "/folder"

_BB_HEADERS = {
    "Authorization": f"Bearer {BB_API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Cache so we don't recreate a KB on every run (there is no list-by-name API).
BB_KB_SLUG_CACHE = Path(os.environ.get("BB_KB_SLUG_CACHE_FILE", "utils/bb_kb_slugs.json"))


# -------------------------
# Knowledge base management
# -------------------------

def create_blockbrain_knowledge_base(
    name: str, description: str = "", **extra
) -> Optional[dict]:
    """Create a new Blockbrain knowledge base and return the parsed response."""
    payload = {"name": name, "description": description, **extra}
    try:
        resp = requests.post(BB_KB_URL, headers=_BB_HEADERS, json=payload, verify=False)
    except Exception as e:
        logging.error("KB creation failed for %r: %s", name, e)
        return None
    if not resp.ok:
        logging.error("KB creation failed for %r: HTTP %s %s", name, resp.status_code, resp.text)
        return None
    logging.info("Created Blockbrain knowledge base %r", name)
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def get_blockbrain_knowledge_base(slug: str) -> Optional[dict]:
    """Fetch a KB description by its slug. Returns the inner body dict or None.

    BB exposes single-KB lookup via ``GET /knowledge_base?knowledge_base=<slug>``.
    There is no list endpoint, so callers must already know the slug
    (typically ``<name>-<uuid>``, e.g. ``atlassian-jira-9f5b...``).
    """
    if not slug:
        return None
    try:
        resp = requests.get(
            BB_KB_URL, params={"knowledge_base": slug}, headers=_BB_HEADERS, verify=False
        )
    except Exception as e:
        logging.error("KB lookup failed for %r: %s", slug, e)
        return None
    if resp.status_code == 404:
        return None
    if not resp.ok:
        logging.error("KB lookup failed for %r: HTTP %s %s", slug, resp.status_code, resp.text)
        return None
    try:
        payload = resp.json() or {}
    except Exception:
        return None
    body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
    return body if isinstance(body, dict) else None


def _extract_kb_slug(payload) -> Optional[str]:
    """Pull the routing slug out of a BB KB response.

    The folder/file APIs route on ``slug`` (``<name>-<uuid>``), NOT on the
    Mongo ``_id``. We therefore prefer ``slug`` over ``_id`` / ``id``.
    """
    if not isinstance(payload, dict):
        return None
    for key in ("slug", "knowledgeBase", "knowledge_base", "knowledgeBaseSlug"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    for nested in ("body", "data"):
        sub = payload.get(nested)
        if isinstance(sub, dict):
            found = _extract_kb_slug(sub)
            if found:
                return found
    return None


def _load_kb_slug_cache() -> Dict[str, str]:
    try:
        return json.loads(BB_KB_SLUG_CACHE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_kb_slug_cache(cache: Dict[str, str]) -> None:
    BB_KB_SLUG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    BB_KB_SLUG_CACHE.write_text(json.dumps(cache, indent=2))


def get_or_create_blockbrain_knowledge_base(
    name: str,
    description: str = "",
    **extra,
) -> Optional[str]:
    """Return the routing **slug** of the KB called ``name``, creating it if missing.

    Resolution order:
      1. If ``name`` already looks like a full slug, the GET succeeds and we
         use it as-is.
      2. Otherwise consult the on-disk slug cache for a previously-created KB
         with this name.
      3. Otherwise create the KB and pull the slug out of the response.
    """
    existing = get_blockbrain_knowledge_base(name)
    if existing:
        slug = _extract_kb_slug(existing) or name
        logging.info("KB %r already exists (slug=%s)", name, slug)
        return slug

    cache = _load_kb_slug_cache()
    cached_slug = cache.get(name)
    if cached_slug:
        existing = get_blockbrain_knowledge_base(cached_slug)
        if existing:
            logging.info("KB %r resolved from cache (slug=%s)", name, cached_slug)
            return cached_slug
        logging.warning("Cached slug %r for KB %r no longer exists, recreating", cached_slug, name)
        cache.pop(name, None)
        _save_kb_slug_cache(cache)

    created = create_blockbrain_knowledge_base(name=name, description=description, **extra)
    slug = _extract_kb_slug(created or {})
    if not slug:
        logging.error("KB created for %r but no slug in response (body=%r)", name, created)
        return None
    cache[name] = slug
    _save_kb_slug_cache(cache)
    logging.info("Created KB %r (slug=%s) and cached it", name, slug)
    return slug


# -------------------------
# Folder management
# -------------------------

def _list_bb_folders(knowledge_base_id: str, parent_path: str = "root") -> List[dict]:
    url = f"{BB_FOLDER_URL}/{knowledge_base_id}/items"
    try:
        resp = requests.post(
            url,
            headers=_BB_HEADERS,
            json={"page": 1, "size": 200, "requestMode": "only_folder", "parentPath": parent_path},
            verify=False,
        )
        if not resp.ok:
            return []
        payload = resp.json() or {}
        envelope = payload.get("body") if isinstance(payload.get("body"), dict) else payload
        return envelope.get("data") or []
    except Exception:
        return []


def get_or_create_blockbrain_folder(
    knowledge_base_id: str,
    name: str,
    parent_path: str = "root",
    description: str = "",
) -> Optional[str]:
    """Return the _id of the named folder, creating it if it does not exist."""
    for item in _list_bb_folders(knowledge_base_id, parent_path):
        item_name = item.get("filename") or item.get("name")
        if item_name == name and (item.get("type") or "folder") == "folder":
            logging.info("Folder %r already exists (id=%s)", name, item.get("_id"))
            return item.get("_id")

    payload = {"name": name, "knowledgeBase": knowledge_base_id, "parentPath": parent_path}
    if description:
        payload["description"] = description
    try:
        resp = requests.post(BB_FOLDER_URL, headers=_BB_HEADERS, json=payload, verify=False)
    except Exception as e:
        logging.error("Folder create failed for %r: %s", name, e)
        return None
    if not resp.ok:
        logging.error("Folder create failed for %r: HTTP %s %s", name, resp.status_code, resp.text)
        return None
    try:
        body = resp.json() or {}
    except Exception:
        body = {}
    for candidate in (body, body.get("body") or {}, (body.get("body") or {}).get("data") or {}):
        for key in ("_id", "id", "folderId"):
            val = (candidate or {}).get(key)
            if isinstance(val, str) and val:
                logging.info("Created folder %r (id=%s)", name, val)
                return val
    # Fallback: re-list and find by name
    for item in _list_bb_folders(knowledge_base_id, parent_path):
        item_name = item.get("filename") or item.get("name")
        if item_name == name:
            return item.get("_id")
    logging.warning("Folder create for %r returned no id", name)
    return None


def get_or_create_folder_path(
    knowledge_base_id: str,
    relative_dir: Path,
    root_parent_path: str = "root",
) -> str:
    """Create (or reuse) every folder in ``relative_dir`` under ``root_parent_path``.

    E.g. ``relative_dir = Path("CORPIT/ISSUE-1/attachments")`` creates the
    three nested folders one level at a time and returns the resulting
    ``parent_path`` string (``root/<id>/<id>/<id>``) to upload files into.
    """
    parent_path = root_parent_path
    for part in relative_dir.parts:
        folder_id = get_or_create_blockbrain_folder(knowledge_base_id, part, parent_path=parent_path)
        if not folder_id:
            logging.warning("Could not create/find folder %r under %s — stopping short", part, parent_path)
            return parent_path
        parent_path = f"{parent_path}/{folder_id}"
    return parent_path


# -------------------------
# File upload
# -------------------------

def upload_file_to_blockbrain(
    knowledge_base_id: str,
    path: Path,
    parent_path: Optional[str] = None,
) -> Optional[dict]:
    """Upload a single file to the Blockbrain /files/v2 endpoint."""
    headers = {"Authorization": f"Bearer {BB_API_KEY}"}
    data = {
        "knowledge_base": knowledge_base_id,
        "parent_path": parent_path if parent_path is not None else BB_PARENT_PATH,
    }
    try:
        with path.open("rb") as f:
            resp = requests.post(
                BB_API_URL,
                headers=headers,
                data=data,
                files={"files": (path.name, f, None)},
                verify=False,
            )
    except Exception as e:
        logging.error("Upload failed for %s: %s", path, e)
        return None

    if not resp.ok:
        logging.error("Upload failed for %s: HTTP %s %s", path, resp.status_code, resp.text)
        return None

    logging.info("Uploaded %s -> %s", path, data["parent_path"])
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def upload_files_with_pause(
    knowledge_base_id: str,
    paths: List[Path],
    sleep: float = UPLOAD_SLEEP,
    parent_path: Optional[str] = None,
):
    """Upload a list of files sequentially with rate-limiting pauses."""
    results = []
    for p in paths:
        res = upload_file_to_blockbrain(knowledge_base_id, p, parent_path=parent_path)
        results.append((p, res))
        time.sleep(sleep)
    return results


# -------------------------
# Driver: mirror a local directory tree into Blockbrain
# -------------------------

def upload_directory_tree(
    knowledge_base_id: str,
    source_dir: Path,
    root_parent_path: str = "root",
) -> Dict[str, Any]:
    """Recreate ``source_dir``'s subfolder structure in Blockbrain and upload
    every file into its corresponding folder.

    Returns a summary dict with counts of folders created and files uploaded.
    """
    folders_created = 0
    files_uploaded = 0
    files_failed: List[str] = []

    for dirpath, _dirnames, filenames in os.walk(source_dir):
        if not filenames:
            continue
        current_dir = Path(dirpath)
        relative_dir = current_dir.relative_to(source_dir)

        if relative_dir == Path("."):
            parent_path = root_parent_path
        else:
            parent_path = get_or_create_folder_path(knowledge_base_id, relative_dir, root_parent_path)
            folders_created += len(relative_dir.parts)

        file_paths = [current_dir / name for name in filenames]
        results = upload_files_with_pause(knowledge_base_id, file_paths, parent_path=parent_path)
        for path, res in results:
            if res is None:
                files_failed.append(str(path))
            else:
                files_uploaded += 1

    return {
        "folders_created": folders_created,
        "files_uploaded": files_uploaded,
        "files_failed": files_failed,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Create/resolve a Blockbrain knowledge base, mirror a local "
        "directory tree as Blockbrain folders, and upload its files."
    )
    parser.add_argument("--source", required=True, help="Local directory to upload")
    parser.add_argument("--kb-name", required=True, help="Knowledge base name or slug")
    parser.add_argument("--kb-description", default="", help="Description used if the KB needs to be created")
    parser.add_argument("--parent-path", default=BB_PARENT_PATH, help="Root parent_path inside the KB (default: root)")
    args = parser.parse_args()

    source_dir = Path(args.source)
    if not source_dir.is_dir():
        raise SystemExit(f"Source directory not found: {source_dir}")

    kb_slug = get_or_create_blockbrain_knowledge_base(
        args.kb_name,
        description=args.kb_description,
        embeddingModel=BB_EMBEDDING_MODEL,
        enableExtractImage=BB_ENABLE_EXTRACT_IMAGE,
    )
    if not kb_slug:
        raise SystemExit(f"Could not resolve or create Blockbrain knowledge base {args.kb_name!r}")

    logging.info("Uploading %s -> KB %s (parent_path=%s)", source_dir, kb_slug, args.parent_path)
    summary = upload_directory_tree(kb_slug, source_dir, root_parent_path=args.parent_path)
    logging.info(
        "Done — folders created: %d, files uploaded: %d, failed: %d",
        summary["folders_created"], summary["files_uploaded"], len(summary["files_failed"]),
    )
    if summary["files_failed"]:
        logging.warning("Failed uploads: %s", summary["files_failed"])


if __name__ == "__main__":
    main()
