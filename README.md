# blockbrain-utils

Utility library for generating images via the [BlockBrain](https://theblockbrain.ai) platform.
Two implementations are provided — one using the **blockbrain_api SDK** and one using **pure REST/requests**.

### Key Features

- **Single-image generation** — upload a local image, send a prompt, and download the generated result.
- **Knowledge-base batch processing** — automatically iterate over all images stored in a BlockBrain knowledge base, use each as input for generation, and save the results locally.
- **Prompt from file** — read the user prompt from a `.user-prompt` file for reproducible runs.

## Installation

### From source

```bash
git clone https://github.com/bmerkle/blockbrain-utils.git
cd blockbrain-utils
pip install -e .
```

### For development

```bash
pip install -e ".[dev]"
```

## Configuration

The library reads its settings from environment variables (a `.env` file is loaded automatically).
Copy `.env-example` to `.env` and fill in your values.
You can also pass values explicitly via `BlockBrainConfig`:

| Variable | Description | Default |
|---|---|---|
| `BLOCKBRAIN_API_BASE` | Base URL of the BlockBrain API | `https://blocky.theblockbrain.ai` |
| `BLOCKBRAIN_API_TOKEN` | Bearer token for authentication | — |
| `BLOCKBRAIN_BOT_ID` | Bot / assistant ID | — |
| `BLOCKBRAIN_KNOWLEDGE_BASE_ID` | Knowledge-base slug (used with `-k`) | — |
| `BLOCKBRAIN_CHAT_MODEL` | Chat model identifier | `google-gemini-2.5-flash` |
| `BLOCKBRAIN_IMAGE_MODEL` | Image model identifier | `google-vertex-25-flash-image` |
| `BLOCKBRAIN_TENANT_DOMAIN` | Tenant domain | `your domain` |

## Usage

### CLI Options

| Flag | Long | Description | Default |
|---|---|---|---|
| `-u` | `--upload-attachment` | Upload a local image before sending the prompt | `false` |
| `-p` | `--path` | Directory containing the image to upload | `image_input` |
| `-f` | `--file` | Filename of the image to upload (used with `-u`) | — |
| `-o` | `--output` | Destination directory/path for generated images | `image_output` |
| `-i` | `--instructions` | Read the prompt from `.user-prompt` instead of `BLOCKBRAIN_USER_PROMPT` | `false` |
| `-k` | `--knowledge-base` | Process all images from the configured knowledge base | `false` |

### Generate images from a knowledge base

Download every image in the knowledge base, use each as input with the prompt from `.user-prompt`, and save results to `image_output/`:

```bash
python src/blockbrain_utils/bb_generate_image_REST.py -i -k
```

The workflow for each image is:
1. Download the document from the knowledge base.
2. Create a new data room and upload the image as an attachment.
3. Send the user prompt referencing the uploaded image.
4. Download the generated image and save it as `<stem>_generated.png`.

### Generate an image from a local file

```bash
python src/blockbrain_utils/bb_generate_image_REST.py -u -p image_input -f photo.jpg -i
```

### Generate without an input image

```bash
python src/blockbrain_utils/bb_generate_image_REST.py -i -o image_output
```

### Prompt file

Create a `.user-prompt` file (or copy `.user-prompt-example`) in the project root with your prompt text. Use the `-i` flag to read from it:

```text
Generate a photo-realistic rendering of the product on a white background.
```

### SDK-based client (recommended)

```python
from blockbrain_utils import BlockBrainConfig, generate_blockbrain_image

cfg = BlockBrainConfig(api_token="your-token", bot_id="your-bot-id")
generate_blockbrain_image(
    prompt="Describe this image",
    image_path="image_input/photo.jpg",
    filename="image_output/result.png",
    cfg=cfg,
)
```

### REST-based client

```python
from blockbrain_utils.bb_generate_image_REST import (
    BlockBrainConfig,
    generate_blockbrain_image,
)

cfg = BlockBrainConfig(api_token="your-token", bot_id="your-bot-id")
generate_blockbrain_image(
    prompt="Describe this image",
    image_path="image_input/photo.jpg",
    filename="image_output/result.png",
    cfg=cfg,
)
```

### Knowledge-base client (programmatic)

```python
from blockbrain_utils.bb_generate_image_REST import (
    BlockBrainConfig,
    generate_images_from_knowledge_base,
)

cfg = BlockBrainConfig(api_token="your-token", bot_id="your-bot-id",
                       knowledge_base_id="my-kb-slug")
generate_images_from_knowledge_base(
    prompt="Generate a clean product shot",
    output_dir="image_output",
    cfg=cfg,
)
```

### Extracting the signed URL

```python
from blockbrain_utils import extract_signed_url

url = extract_signed_url(api_response_text)
```

## Development

### Running tests

```bash
pytest
```

### Running tests with coverage

```bash
pytest --cov=blockbrain_utils --cov-report=html
```

## Project Structure

```
blockbrain-utils/
├── src/
│   └── blockbrain_utils/
│       ├── __init__.py
│       ├── bb_generate_image_API.py   # SDK-based client
│       └── bb_generate_image_REST.py  # Pure REST client
├── tests/
│   ├── __init__.py
│   ├── test_bb_generate_image_API.py
│   └── data/
│       └── example.jpg
├── image_input/                       # Default input directory (images to process)
│   └── .gitkeep
├── image_output/                      # Default output directory (generated images)
│   └── .gitkeep
├── .env-example                       # Template for environment variables
├── .user-prompt-example               # Template for the prompt file
├── pyproject.toml
├── README.md
├── REPORT-pythonAPI-vs-REST-report.md
├── LICENSE
└── .gitignore
```

## License

MIT License — see LICENSE file for details.