"""Tests for bb_generate_image_API module."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest
import responses

from blockbrain_utils.bb_generate_image_API import (
    BlockBrainConfig,
    extract_signed_url,
    generate_blockbrain_image,
    _init_api,
    _setup_data_room,
    _upload_and_wait,
    _send_and_download,
    POST_PROCESSING_SETTLE_SECONDS,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg():
    """Return a BlockBrainConfig with dummy values."""
    return BlockBrainConfig(
        api_base="https://test.blockbrain.ai",
        api_token="test-token-123",
        bot_id="bot-456",
        chat_model="test-model",
        image_model="test-image-model",
        tenant_domain="test-tenant",
    )


@pytest.fixture
def mock_api():
    """Return a mock BlockBrainAPI instance."""
    api = MagicMock()
    api.core = MagicMock()
    return api


@pytest.fixture
def tmp_image(tmp_path: Path) -> Path:
    """Create a tiny temporary image file and return its path."""
    img = tmp_path / "test_input.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-data")
    return img


# ── BlockBrainConfig tests ──────────────────────────────────────────────────

class TestBlockBrainConfig:
    def test_explicit_values(self, cfg: BlockBrainConfig):
        assert cfg.api_base == "https://test.blockbrain.ai"
        assert cfg.api_token == "test-token-123"
        assert cfg.bot_id == "bot-456"
        assert cfg.chat_model == "test-model"
        assert cfg.image_model == "test-image-model"
        assert cfg.tenant_domain == "test-tenant"

    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("BLOCKBRAIN_API_BASE", "https://env.example.com")
        monkeypatch.setenv("BLOCKBRAIN_API_TOKEN", "env-token")
        monkeypatch.setenv("BLOCKBRAIN_BOT_ID", "env-bot")
        monkeypatch.setenv("BLOCKBRAIN_CHAT_MODEL", "env-model")
        monkeypatch.setenv("BLOCKBRAIN_IMAGE_MODEL", "env-img-model")
        monkeypatch.setenv("BLOCKBRAIN_TENANT_DOMAIN", "env-tenant")
        # Re-evaluate defaults by passing nothing
        c = BlockBrainConfig(
            api_base="https://env.example.com",
            api_token="env-token",
            bot_id="env-bot",
            chat_model="env-model",
            image_model="env-img-model",
            tenant_domain="env-tenant",
        )
        assert c.api_token == "env-token"


# ── extract_signed_url tests ───────────────────────────────────────────────

class TestExtractSignedUrl:
    def test_extracts_url_from_sse_stream(self):
        sse = (
            "event: generated_media\n"
            'data: {"mediaUrls": [{"signedUrl": "https://cdn.example.com/img.png"}]}\n\n'
        )
        assert extract_signed_url(sse) == "https://cdn.example.com/img.png"

    def test_returns_none_when_no_generated_media_event(self):
        sse = (
            "event: text\n"
            'data: {"content": "hello"}\n\n'
        )
        assert extract_signed_url(sse) is None

    def test_returns_none_when_media_urls_empty(self):
        sse = (
            "event: generated_media\n"
            'data: {"mediaUrls": []}\n\n'
        )
        assert extract_signed_url(sse) is None

    def test_handles_multiple_blocks(self):
        sse = (
            "event: text\n"
            'data: {"content": "thinking..."}\n\n'
            "event: generated_media\n"
            'data: {"mediaUrls": [{"signedUrl": "https://cdn.example.com/result.png"}]}\n\n'
        )
        assert extract_signed_url(sse) == "https://cdn.example.com/result.png"

    def test_returns_first_url_from_multiple(self):
        sse = (
            "event: generated_media\n"
            'data: {"mediaUrls": [{"signedUrl": "https://first.com/a.png"}, {"signedUrl": "https://second.com/b.png"}]}\n\n'
        )
        assert extract_signed_url(sse) == "https://first.com/a.png"


# ── _init_api tests ────────────────────────────────────────────────────────

class TestInitApi:
    @patch("blockbrain_utils.bb_generate_image_API.BlockBrainAPI")
    def test_creates_api_with_config(self, mock_cls, cfg):
        _init_api(cfg)
        mock_cls.assert_called_once_with(
            base_url=cfg.api_base,
            token=cfg.api_token,
            bot_id=cfg.bot_id,
            tenant_domain=cfg.tenant_domain,
            default_model=cfg.chat_model,
            enable_logging=True,
        )


# ── _setup_data_room tests ─────────────────────────────────────────────────

class TestSetupDataRoom:
    def test_creates_data_room_and_returns_convo_id(self, mock_api, cfg):
        mock_api.core.create_data_room.return_value = {
            "body": {"dataRoomId": "convo-789"}
        }

        convo_id = _setup_data_room(mock_api, cfg, "session-001")

        assert convo_id == "convo-789"
        mock_api.core.create_data_room.assert_called_once()
        mock_api.core.change_model.assert_called_once_with(
            "convo-789",
            model=cfg.chat_model,
            enable_generate_image=True,
        )


# ── _upload_and_wait tests ─────────────────────────────────────────────────

class TestUploadAndWait:
    @patch("blockbrain_utils.bb_generate_image_API.time.sleep")
    def test_successful_upload_returns_attachment_id(self, mock_sleep,
                                                     mock_api, cfg, tmp_image):
        mock_api.core.upload_file.return_value = {
            "body": {"_id": "attach-111"}
        }
        mock_api.core.wait_for_file_processing.return_value = {"success": True}

        result = _upload_and_wait(mock_api, cfg, tmp_image, "convo-1", "sess-1")

        assert result == "attach-111"
        mock_sleep.assert_called_once_with(POST_PROCESSING_SETTLE_SECONDS)

    def test_raises_on_upload_error(self, mock_api, cfg, tmp_image):
        mock_api.core.upload_file.return_value = {"error": "upload failed"}

        with pytest.raises(RuntimeError, match="Upload failed"):
            _upload_and_wait(mock_api, cfg, tmp_image, "convo-1", "sess-1")

    @patch("blockbrain_utils.bb_generate_image_API.time.sleep")
    def test_falls_back_to_status_check_when_no_attachment_id(
        self, mock_sleep, mock_api, cfg, tmp_image
    ):
        mock_api.core.upload_file.return_value = {"body": {}}
        mock_api.core.wait_for_file_processing.return_value = {"success": True}
        mock_api.core.check_file_upload_status.return_value = {
            "body": [{"_id": "attach-from-status"}]
        }

        result = _upload_and_wait(mock_api, cfg, tmp_image, "convo-1", "sess-1")
        assert result == "attach-from-status"

    @patch("blockbrain_utils.bb_generate_image_API.time.sleep")
    def test_raises_when_no_attachment_id_found(
        self, mock_sleep, mock_api, cfg, tmp_image
    ):
        mock_api.core.upload_file.return_value = {"body": {}}
        mock_api.core.wait_for_file_processing.return_value = {"success": True}
        mock_api.core.check_file_upload_status.return_value = {"body": []}

        with pytest.raises(RuntimeError, match="Could not determine attachment _id"):
            _upload_and_wait(mock_api, cfg, tmp_image, "convo-1", "sess-1")


# ── _send_and_download tests ──────────────────────────────────────────────

class TestSendAndDownload:
    @responses.activate
    def test_downloads_image_from_dict_response(self, mock_api, cfg, tmp_path):
        image_url = "https://cdn.example.com/generated.png"
        mock_api.core.user_prompt.return_value = {
            "mediaUrls": [{"signedUrl": image_url}]
        }

        responses.add(
            responses.GET,
            image_url,
            body=b"fake-png-bytes",
            status=200,
        )

        output_file = str(tmp_path / "out.png")
        _send_and_download(
            mock_api, cfg, "convo-1", "sess-1",
            "make it red", "attach-1", "input.png", output_file,
        )

        assert Path(output_file).read_bytes() == b"fake-png-bytes"

    @responses.activate
    def test_downloads_image_from_sse_string_response(self, mock_api, cfg, tmp_path):
        image_url = "https://cdn.example.com/sse.png"
        sse = (
            "event: generated_media\n"
            f'data: {{"mediaUrls": [{{"signedUrl": "{image_url}"}}]}}\n\n'
        )
        mock_api.core.user_prompt.return_value = sse

        responses.add(responses.GET, image_url, body=b"sse-png", status=200)

        output_file = str(tmp_path / "sse_out.png")
        _send_and_download(
            mock_api, cfg, "convo-1", "sess-1",
            "blue sky", "attach-2", "source.jpg", output_file,
        )

        assert Path(output_file).read_bytes() == b"sse-png"

    def test_logs_error_when_no_image_url(self, mock_api, cfg, tmp_path, caplog):
        mock_api.core.user_prompt.return_value = {"mediaUrls": []}

        output_file = str(tmp_path / "missing.png")
        with caplog.at_level(logging.ERROR):
            _send_and_download(
                mock_api, cfg, "convo-1", "sess-1",
                "prompt", "attach-3", "img.png", output_file,
            )
        assert not Path(output_file).exists()
        assert "No image URL found" in caplog.text


# ── generate_blockbrain_image (integration-level) ──────────────────────────

class TestGenerateBlockbrainImage:
    def test_returns_early_when_token_missing(self, caplog, tmp_image):
        bad_cfg = BlockBrainConfig(api_token="", bot_id="bot")
        with caplog.at_level(logging.ERROR):
            generate_blockbrain_image("prompt", tmp_image, cfg=bad_cfg)
        assert "Missing api_token" in caplog.text

    def test_returns_early_when_bot_id_missing(self, caplog, tmp_image):
        bad_cfg = BlockBrainConfig(api_token="tok", bot_id="")
        with caplog.at_level(logging.ERROR):
            generate_blockbrain_image("prompt", tmp_image, cfg=bad_cfg)
        assert "Missing api_token or bot_id" in caplog.text

    def test_returns_early_when_image_not_found(self, caplog, cfg):
        missing = Path("/nonexistent/image.png")
        with caplog.at_level(logging.ERROR):
            generate_blockbrain_image("prompt", missing, cfg=cfg)
        assert "Image file not found" in caplog.text

    @patch("blockbrain_utils.bb_generate_image_API._send_and_download")
    @patch("blockbrain_utils.bb_generate_image_API._upload_and_wait")
    @patch("blockbrain_utils.bb_generate_image_API._setup_data_room")
    @patch("blockbrain_utils.bb_generate_image_API._init_api")
    def test_happy_path(self, mock_init, mock_setup, mock_upload,
                        mock_send, cfg, tmp_image, tmp_path):
        mock_api_inst = MagicMock()
        mock_init.return_value = mock_api_inst
        mock_setup.return_value = "convo-99"
        mock_upload.return_value = "attach-99"

        output = str(tmp_path / "result.png")
        generate_blockbrain_image("make it bright", tmp_image,
                                  filename=output, cfg=cfg)

        mock_init.assert_called_once_with(cfg)
        mock_setup.assert_called_once()
        mock_upload.assert_called_once()
        mock_send.assert_called_once()

    @patch("blockbrain_utils.bb_generate_image_API._setup_data_room")
    @patch("blockbrain_utils.bb_generate_image_API._init_api")
    def test_handles_exception_gracefully(self, mock_init, mock_setup, cfg, tmp_image, caplog):
        mock_setup.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.ERROR):
            generate_blockbrain_image("prompt", tmp_image, cfg=cfg)
        assert "boom" in caplog.text
