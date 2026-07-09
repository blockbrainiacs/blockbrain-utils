# Blockbrain KB Upload

Standalone script that mirrors a local directory tree into a Blockbrain knowledge base: it creates (or resolves) the knowledge base, recreates the source directory's subfolder structure as Blockbrain folders, and uploads every file into the matching folder.

Extracted from the Blockbrain upload / KB-management cells of `jira-BB-SupplyOn_adapter.ipynb` so the logic can be reused outside a notebook.

## Contents

- `blockbrain_kb_upload.py` — the upload script and its CLI entry point.
- `.env.upload.example` — template for the script's configuration file.
- `requirements.txt` — Python dependencies.

## Requirements

- Python 3.9+
- A Blockbrain API key with access to the target tenant.

## Setup

```bash
pip install -r requirements.txt
cp .env.upload.example .env.upload
```

Edit `.env.upload` and fill in your Blockbrain API URL and API key. This file is loaded automatically by the script and is separate from any shared project `.env` file.

### Configuration (`.env.upload`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `BB_API_URL` | yes | — | Blockbrain files endpoint, e.g. `https://blocky.theblockbrain.ai/files/v2` |
| `BB_API_KEY` | yes | — | Blockbrain API key |
| `BB_EMBEDDING_MODEL` | no | `azure-emb-3-large` | Embedding model used only when a new KB is created |
| `BB_ENABLE_EXTRACT_IMAGE` | no | `true` | `true`/`false`, used only when a new KB is created |
| `BB_PARENT_PATH` | no | `root` | Root parent path inside the KB to upload into |
| `UPLOAD_SLEEP` | no | `0.1` | Seconds to pause between file uploads |
| `BB_KB_SLUG_CACHE_FILE` | no | `utils/bb_kb_slugs.json` | Where resolved KB name → slug mappings are cached, so a KB isn't recreated on every run |

## Usage

```bash
python blockbrain_kb_upload.py --source DB_content_jira/CORPIT --kb-name atlassian-jira
```

Arguments:

- `--source` (required) — local directory to upload.
- `--kb-name` (required) — knowledge base name or slug; created if it doesn't exist.
- `--kb-description` (optional) — description used only if the KB has to be created.
- `--parent-path` (optional) — root parent path inside the KB (default: `root`).

The script walks `--source` recursively, creating a matching folder for every subdirectory that contains files, then uploads each file into place with a short pause between uploads. It prints a summary of folders created, files uploaded, and any failed uploads.

## Notes

- There is no Blockbrain API to list knowledge bases by name, so resolved KB name → slug mappings are cached on disk (`BB_KB_SLUG_CACHE_FILE`) to avoid recreating a KB on every run.
- TLS certificate verification is disabled for requests to the Blockbrain API (`verify=False`); only point this script at trusted Blockbrain endpoints.
