from __future__ import annotations

"""BlockBrain image generation — uses the blockbrain_api SDK."""

import argparse
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests
from blockbrain_api import BlockBrainAPI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

POST_PROCESSING_SETTLE_SECONDS = 5
"""Seconds to wait after file processing completes, allowing indexing to commit."""


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass
class BlockBrainConfig:
    """All settings required to talk to the BlockBrain API via the SDK.

    Defaults are drawn from environment variables at import time.
    Pass explicit values to override.
    """

    api_base: str = os.getenv("BLOCKBRAIN_API_BASE", "https://blocky.theblockbrain.ai")
    api_token: str = os.getenv("BLOCKBRAIN_API_TOKEN", "")
    bot_id: str = os.getenv("BLOCKBRAIN_BOT_ID", "")
    knowledge_base_id: str = os.getenv("BLOCKBRAIN_KNOWLEDGE_BASE_ID", "")
    chat_model: str = os.getenv("BLOCKBRAIN_CHAT_MODEL", "google-gemini-2.5-flash")
    image_model: str = os.getenv("BLOCKBRAIN_IMAGE_MODEL", "google-vertex-25-flash-image")
    tenant_domain: str = os.getenv("BLOCKBRAIN_TENANT_DOMAIN", "my-default-tenant")


# ── Orchestration helpers ───────────────────────────────────────────────────


def _build_session(cfg: BlockBrainConfig) -> requests.Session:
    """Create a requests.Session for knowledge-base REST calls."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {cfg.api_token}",
        "Content-Type": "application/json",
    })
    if cfg.tenant_domain:
        origin = f"https://{cfg.tenant_domain}.kb.theblockbrain.ai"
        session.headers["Referer"] = f"{origin}/"
        session.headers["Origin"] = origin
    return session


def _api_url(cfg: BlockBrainConfig, path: str) -> str:
    """Build a full URL from a relative API path."""
    return f"{cfg.api_base.rstrip('/')}{path}"


def _kb_url(cfg: BlockBrainConfig, path: str) -> str:
    """Build a full URL for the tenant knowledge-base domain.

    Knowledge-base operations live on
    ``https://{tenant_domain}.kb.theblockbrain.ai``.
    """
    base = f"https://{cfg.tenant_domain}.kb.theblockbrain.ai"
    return f"{base}{path}"


# ── Knowledge-base helpers ──────────────────────────────────────────────────

def check_bot_knowledge_base_connection(
    session: requests.Session, cfg: BlockBrainConfig, kb_id: str,
) -> bool:
    """Verify that the knowledge base *kb_id* exists and is reachable.

    Uses ``GET /document-management/knowledgebase/{kb_id}`` on the tenant
    KB domain (``https://{tenant}.kb.theblockbrain.ai``).
    Returns ``True`` when the endpoint responds successfully.
    """
    url = _kb_url(cfg, f"/document-management/knowledgebase/{kb_id}")
    resp = session.get(url)
    if resp.status_code >= 400:
        logger.error(
            "Knowledge base '%s' not reachable (HTTP %d): %s",
            kb_id, resp.status_code, resp.text[:500],
        )
        return False
    logger.info("Knowledge base '%s' is reachable.", kb_id)
    return True


def list_knowledge_base_documents(
    session: requests.Session, cfg: BlockBrainConfig, kb_id: str,
) -> list[dict]:
    """Return all documents in the knowledge base *kb_id*.

    Uses ``GET /knowledge_base/{knowledge_base}/documents``.
    """
    resp = session.get(_kb_url(cfg, f"/knowledge_base/{kb_id}/documents"))
    resp.raise_for_status()
    result = resp.json()
    docs = result if isinstance(result, list) else result.get("body", result)
    if isinstance(docs, list):
        logger.info("Found %d document(s) in knowledge base '%s'.", len(docs), kb_id)
    else:
        docs = []
    return docs


def download_document(
    session: requests.Session, cfg: BlockBrainConfig, document_id: str,
    dest_path: Path,
) -> Path:
    """Download a document by its ``_id`` and save it to *dest_path*.

    The ``GET /files/download/{document_id}`` endpoint returns a JSON
    envelope with a presigned media URL.  This function resolves that URL
    and streams the actual file content to *dest_path*.
    """
    resp = session.get(_kb_url(cfg, f"/files/download/{document_id}"))
    resp.raise_for_status()
    data = resp.json()
    media_url: str = data.get("body", "") if isinstance(data, dict) else ""
    if not media_url:
        raise RuntimeError(
            f"No media URL in download response for {document_id}: {data}"
        )

    logger.debug("Resolved media URL: %s", media_url)
    media_resp = session.get(media_url, stream=True)
    media_resp.raise_for_status()

    with open(dest_path, "wb") as fh:
        for chunk in media_resp.iter_content(chunk_size=8192):
            fh.write(chunk)
    logger.info("Downloaded document %s → %s (%d bytes)",
                document_id, dest_path.name, dest_path.stat().st_size)
    return dest_path

def _init_api(cfg: BlockBrainConfig) -> BlockBrainAPI:
    """Create and return a configured BlockBrainAPI client."""
    return BlockBrainAPI(
        base_url=cfg.api_base,
        token=cfg.api_token,
        bot_id=cfg.bot_id,
        tenant_domain=cfg.tenant_domain,
        default_model=cfg.chat_model,
        enable_logging=True,
    )


def _setup_data_room(api: BlockBrainAPI, cfg: BlockBrainConfig,
                     session_id: str,
                     knowledge_base_ids: list[str] | None = None) -> str:
    """Create a data room, configure model + image generation.

    When *knowledge_base_ids* is provided the listed knowledge bases are
    connected to the conversation so the model can access their documents
    directly.

    Returns the ``convo_id``.
    """
    convo_name = f"image_gen_{session_id[:8]}"

    logger.info("[1] Creating data room …")
    dr = api.core.create_data_room(
        convo_name, session_id, cfg.bot_id, model=cfg.chat_model
    )
    convo_id: str = dr["body"]["dataRoomId"]
    logger.info("    convo_id = %s", convo_id)

    logger.info("[2] Setting model …")
    api.core.change_model(
        convo_id,
        model=cfg.chat_model,
        enable_generate_image=True,
        knowledge_base=knowledge_base_ids,
    )
    kb_info = f"  |  KB: {knowledge_base_ids}" if knowledge_base_ids else ""
    logger.info("    Chat model: %s  (image gen enabled)%s", cfg.chat_model, kb_info)

    return convo_id


def _upload_and_wait(api: BlockBrainAPI, cfg: BlockBrainConfig,
                     image_path: Path, convo_id: str,
                     session_id: str) -> str:
    """Upload *image_path*, wait for processing, return the attachment ``_id``."""
    logger.info("[3] Uploading attachment: %s", image_path.resolve())
    upload_result = api.core.upload_file(str(image_path), convo_id, session_id)

    if isinstance(upload_result, dict) and upload_result.get("error"):
        raise RuntimeError("Upload failed")

    attachment_id: str = upload_result.get("body", {}).get("_id", "")
    logger.info("    attachment_id = %s", attachment_id)

    logger.info("[4] Waiting for attachment processing …")
    processing_result = api.core.wait_for_file_processing(
        convo_id, timeout=300, poll_interval=3
    )

    processing_ok = (
        isinstance(processing_result, dict)
        and processing_result.get("success")
    )

    if processing_ok:
        logger.info("File processed successfully.")
        time.sleep(POST_PROCESSING_SETTLE_SECONDS)

        if not attachment_id:
            status = api.core.check_file_upload_status(convo_id)
            files_list = (
                status.get("body", []) if isinstance(status, dict) else []
            )
            if isinstance(files_list, list) and files_list:
                attachment_id = files_list[0].get("_id", "")
                logger.info("    attachment_id (from status) = %s", attachment_id)
    else:
        error_detail = (
            processing_result.get("error", "unknown")
            if isinstance(processing_result, dict)
            else str(processing_result)
        )
        logger.warning("File processing may not have completed: %s", error_detail)

    if not attachment_id:
        raise RuntimeError("Could not determine attachment _id")

    return attachment_id


def _send_and_download(api: BlockBrainAPI, cfg: BlockBrainConfig,
                       convo_id: str, session_id: str,
                       prompt: str, attachment_id: str | None,
                       uploaded_filename: str | None, filename: str) -> None:
    """Send the image-generation prompt, download the result, save to *filename*."""
    if uploaded_filename and attachment_id:
        full_prompt = f"Look at the file named {uploaded_filename}. {prompt}"
    else:
        full_prompt = prompt

    logger.info("[5] Sending image-generation prompt …")
    logger.debug("    files (attachment IDs): [%s]", attachment_id)

    files_list = [attachment_id] if attachment_id else None
    raw_response = api.core.user_prompt(
        content=full_prompt,
        session_id=session_id,
        convo_id=convo_id,
        model=cfg.chat_model,
        files=files_list,
        stream=False,
    )
    logger.info("Prompt response received.")

    # The SDK may return parsed JSON (dict/str) rather than raw SSE text.
    if isinstance(raw_response, str):
        image_url = extract_signed_url(raw_response)
    elif isinstance(raw_response, dict):
        media_urls = raw_response.get("mediaUrls", [])
        image_url = media_urls[0].get("signedUrl") if media_urls else None
    else:
        image_url = None

    if not image_url:
        response_preview = (
            raw_response[:2000] if isinstance(raw_response, str)
            else json.dumps(raw_response, indent=2)[:2000]
        )
        logger.error("No image URL found in response.\n%s", response_preview)
        return

    logger.info("Downloading image from: %s", image_url)
    headers = {"Authorization": f"Bearer {cfg.api_token}"}
    img_resp = requests.get(image_url, headers=headers, stream=True)
    img_resp.raise_for_status()

    with open(filename, "wb") as f:
        for chunk in img_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("Successfully saved image to %s", filename)


# ── SSE response parsing ───────────────────────────────────────────────────

def extract_signed_url(raw_stream_data: str) -> str | None:
    """Parse SSE stream text and return the first signedUrl from a
    ``generated_media`` event."""
    blocks = raw_stream_data.strip().split("\n\n")

    for block in blocks:
        if "event: generated_media" in block:
            for line in block.split("\n"):
                if line.startswith("data: "):
                    json_str = line[6:].strip()
                    data_obj = json.loads(json_str)
                    media_urls = data_obj.get("mediaUrls", [])
                    if media_urls:
                        return media_urls[0].get("signedUrl")

    return None


# ── Main flow ───────────────────────────────────────────────────────────────

def generate_blockbrain_image(
    prompt: str,
    image_path: Path | None = None,
    filename: str = "output_image.png",
    cfg: BlockBrainConfig | None = None,
) -> None:
    """Generate an image using the BlockBrain API SDK.

    Parameters
    ----------
    prompt:
        The text instruction describing the desired image modification.
    image_path:
        Path to the source image to upload as context.  When *None* the
        prompt is sent without an attachment.
    filename:
        Destination path for the generated image.
    cfg:
        Optional configuration; defaults are loaded from environment variables.
    """
    if cfg is None:
        cfg = BlockBrainConfig()

    if not cfg.api_token or not cfg.bot_id:
        logger.error("Missing api_token or bot_id in config.")
        return

    if image_path is not None and not image_path.exists():
        logger.error("Image file not found: %s", image_path.absolute())
        return

    logger.info("--- BlockBrain image generation (SDK) ---")
    logger.info("    Base URL: %s  |  Chat model: %s  |  Image model: %s",
                cfg.api_base, cfg.chat_model, cfg.image_model)

    api = _init_api(cfg)

    try:
        session_id = str(uuid.uuid4())
        convo_id = _setup_data_room(api, cfg, session_id)

        attachment_id: str | None = None
        uploaded_filename: str | None = None
        if image_path is not None:
            attachment_id = _upload_and_wait(api, cfg, image_path, convo_id, session_id)
            uploaded_filename = image_path.name
        else:
            logger.info("[3-4] Skipping attachment upload (no image provided).")

        _send_and_download(api, cfg, convo_id, session_id,
                           prompt, attachment_id, uploaded_filename, filename)
    except Exception as e:
        logger.exception("An error occurred: %s", e)


def generate_images_from_knowledge_base(
    prompt: str,
    output_dir: str = ".",
    cfg: BlockBrainConfig | None = None,
) -> None:
    """Process every image in the configured knowledge base.

    For each document in the KB the function creates a fresh data-room,
    uploads the image, sends *prompt*, and downloads the generated result
    into *output_dir*.
    """
    if cfg is None:
        cfg = BlockBrainConfig()

    if not cfg.api_token or not cfg.bot_id:
        logger.error("Missing api_token or bot_id in config.")
        return
    if not cfg.knowledge_base_id:
        logger.error("Missing knowledge_base_id in config (set BLOCKBRAIN_KNOWLEDGE_BASE_ID).")
        return

    session = _build_session(cfg)

    # 0. Verify relationship
    if not check_bot_knowledge_base_connection(session, cfg, cfg.knowledge_base_id):
        return

    # 1. List all documents in the KB
    documents = list_knowledge_base_documents(session, cfg, cfg.knowledge_base_id)
    if not documents:
        logger.warning("No documents found in knowledge base '%s'.", cfg.knowledge_base_id)
        return

    os.makedirs(output_dir, exist_ok=True)

    logger.info("--- Processing %d image(s) from knowledge base (SDK) ---", len(documents))

    api = _init_api(cfg)

    with tempfile.TemporaryDirectory(prefix="bb_kb_") as tmp_dir:
        for idx, doc in enumerate(documents, start=1):
            doc_id = doc.get("_id", "")
            doc_name = doc.get("filename", doc.get("name", f"image_{idx}"))
            logger.info("\n=== [%d/%d] Processing: %s (id=%s) ===",
                        idx, len(documents), doc_name, doc_id)

            # Build an output filename from the original name
            stem = Path(doc_name).stem
            out_file = os.path.join(output_dir, f"{stem}_generated.png")

            try:
                # Download the KB document to a temp file
                tmp_path = Path(tmp_dir) / doc_name
                download_document(session, cfg, doc_id, tmp_path)

                # Create a data room, upload the image, and generate
                session_id = str(uuid.uuid4())
                convo_id = _setup_data_room(api, cfg, session_id)
                attachment_id = _upload_and_wait(
                    api, cfg, tmp_path, convo_id, session_id
                )
                _send_and_download(
                    api, cfg, convo_id, session_id,
                    prompt, attachment_id, doc_name, out_file,
                )
            except Exception as exc:
                logger.exception("Failed to process '%s': %s", doc_name, exc)

    logger.info("--- Knowledge-base processing complete ---")


# ── CLI entry point ─────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an image via the BlockBrain SDK.",
    )
    parser.add_argument(
        "-u", "--upload-attachment",
        action="store_true",
        default=False,
        help="Upload an image attachment before sending the prompt.",
    )
    parser.add_argument(
        "-p", "--path",
        type=Path,
        default=Path("image_input"),
        help="Directory containing the image to upload (default: image_input).",
    )
    parser.add_argument(
        "-f", "--file",
        type=str,
        default=None,
        help="Filename of the image to upload (used with --upload-attachment).",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="image_output",
        help="Destination path for the generated image (default: image_output).",
    )
    parser.add_argument(
        "-i", "--instructions",
        action="store_true",
        default=False,
        help="Read the user prompt from a .user-prompt file instead of the BLOCKBRAIN_USER_PROMPT env variable.",
    )
    parser.add_argument(
        "-k", "--knowledge-base",
        action="store_true",
        default=False,
        help="Use images from the configured knowledge base (BLOCKBRAIN_KNOWLEDGE_BASE_ID) as input.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    args = _parse_args()

    if args.instructions:
        prompt_file = Path(".user-prompt")
        if not prompt_file.exists():
            logger.error(".user-prompt file not found")
            raise SystemExit(1)
        user_prompt = prompt_file.read_text(encoding="utf-8").strip()
    else:
        user_prompt = os.getenv("BLOCKBRAIN_USER_PROMPT", "")

    if not user_prompt:
        logger.error("No user prompt provided (set BLOCKBRAIN_USER_PROMPT or use -i with a .user-prompt file)")
        raise SystemExit(1)

    if args.knowledge_base:
        # Process all images from the knowledge base
        generate_images_from_knowledge_base(
            user_prompt, output_dir=args.output,
        )
    else:
        image_path: Path | None = None
        if args.upload_attachment:
            if not args.path or not args.file:
                logger.error("-path and -file are required when using --upload-attachment")
                raise SystemExit(1)
            image_path = Path(args.path) / args.file

        generate_blockbrain_image(user_prompt, image_path=image_path, filename=args.output)
