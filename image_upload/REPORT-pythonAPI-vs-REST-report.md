# blockbrain_api SDK vs REST API — Comparison Report

**SDK version**: `blockbrain_api` v0.1.3  
**REST API version**: Blockbrain Knowledge Bots v0.12.1 (OAS 3.1)  
**REST API docs**: https://blocky.theblockbrain.ai/docs  
**Date**: 2026-02-18

---

## 1. REST API endpoint groups with NO SDK coverage

The SDK covers ~10 out of 200+ REST API endpoints. The following entire groups have **zero methods** in the Python library:

| REST API Group | # Endpoints | Description |
|---|---|---|
| **files** | 6 | Upload, download, multi-download documents |
| **knowledge_base** | 15+ | CRUD knowledge bases, search documents, statistics, crop-region templates |
| **folder** | 15+ | Create/move/delete folders, search, duplicate detection, cleanup |
| **share bot access** | 12+ | Sharing bots, access levels, transfer ownership |
| **share document access** | 13+ | Document sharing, access modes |
| **speech_to_text** | 1 | Generate text from speech |
| **cortex bot** | 12+ | Create/update/delete/duplicate bots, tags, sharing links |
| **cortex active-bot** | 10+ | Active bot management (partially covered — only `create_data_room` uses one endpoint) |
| **cortex completions** | 3 | v1 completions (continue-generating, regen) |
| **cortex notes** | 15+ | Generate/add/update/delete/search insights & notes |
| **cortex message** | 9 | Message list, detail, rewind, edit, feedback, delete |
| **cortex summary** | 4 | Chat summaries |
| **cortex workflow** | 10 | Create/update/delete workflows |
| **workflow job** | 3 | Schedule workflow jobs |
| **cortex workflow step** | 5 | Workflow step CRUD |
| **cortex workflow step context** | 4 | Step context CRUD |
| **cortex workflow step output** | 4 | Step output CRUD |
| **cortex executor** | 12+ | Run/stop/pause workflows, save outputs |
| **agent task** | 9 | Agent task CRUD, favorites |
| **agent category** | 5 | Agent category CRUD |
| **email management** | 13 | Gmail/Outlook connect, fetch, save |
| **gdrive management** | 2 | Connect/disconnect Google Drive |
| **onedrive management** | 3 | Connect/disconnect/upload OneDrive |
| **web search management** | 7 | Web search providers |
| **dataroom management** | 12+ | Dataroom CRUD, attachments (SDK uses conversation endpoints instead) |
| **System GenAI management** | 3 | GenAI model management for tenant |
| **cortex completions v2** | 6 | v2 continue-generating, start company bot, message stream status, resume, stop |
| **workflow v2** | 4 | Workflow v2 attachments |
| **s3_management** | 2 | S3 upload |
| **user** | 7 | User info, avatar, language, timezone |
| **web_component** | 11 | Web component bot mappings |
| **group** | 15+ | Group/user management, Azure sync |
| **custom_agents** | 5 | Custom agent CRUD |
| **notification** | 10 | Notification CRUD |
| **speech** | 5 | Voice list, speech generation |
| **user-tenant** | 2 | Tenant domains, feature flags |
| **embedding_model_usage** | 2 | Embedding model usage |
| **global-system** | 2 | Global search |
| **contribution** | 7 | Knowledge base subscriptions/contributions |
| **llm_model_general** | 7 | LLM model config management |
| **embedding_model_general** | 4 | Embedding model config |
| **image_model_general** | 4 | Image model config |
| **video_model_general** | 4 | Video model config |
| **external_bot_request** | 2 | External bot requests |
| **integration** | 15+ | Sharepoint, Azure, GitHub, Google identity providers |
| **export** | 5 | Export insight/message/dataroom |
| **introduction** | 5 | Feature introductions |
| **file management v2** | 8 | v2 file upload (multipart), image from chunk, PDF preview |
| **auth** | 1 | API key introspection |
| **survey** | 3 | Survey webhooks |
| **tenant** | 4 | Tenant details, branding, feature flags |
| **tenant-mobile** | 2 | Tenant by email |
| **oauth-callback** | 5 | OAuth callbacks |
| **default** (readiness/health) | 4 | Health checks, SSE message/workflow streams |

---

## 2. SDK methods vs REST endpoints — inconsistencies

| SDK Method (`BlockBrainCore`) | REST Endpoint Used | Issues |
|---|---|---|
| `user_prompt()` | `POST /cortex/completions/v2/user-input` | **Missing params**: `webSearchConfig`, `advancedFeature`, `enableWebSearch`, `enableReranker`, `chatMode`, `enableAgentRetrieval`, `knowledgeBase` (all in `CortexUserMessageCreateRequest` schema). No option for v1 (`POST /cortex/completions/user-input`). |
| `create_data_room()` | `POST /cortex/active-bot/{bot_id}/convo` | **Missing params**: `CortexAddConvoToBotRequest` may include fields beyond `convoName`, `sessionId`, `defaultLanguage`, `isDefaultConvoName`. No `convoType` or other settings. |
| `upload_file()` | `POST /cortex/conversation/{convo_id}/attachment` | REST also has `POST /cortex/dataroom/{dataroom_id}/attachment`, `POST /files` (v1), `POST /files/v2` (multipart). SDK only supports conversation attachment route. **Missing**: multi-part/chunked upload, upload from Google Drive (`attachment-from-drive`). |
| `add_context()` | `PUT /cortex/conversation/{convo_id}/context` | Matches, but REST also has `GET /cortex/conversation/{convo_id}/context` — SDK cannot **read** context. |
| `change_model()` | `PATCH /cortex/conversation/{convo_id}` | Actually the **update conversation** endpoint. SDK exposes 14 params but **missing REST params**: `advance_options`, `language`, `enableImageCrop`, `ocrApiModel`, `strategies`, `embeddingModel`, `custom_system_prompt`, `selectedWorkflow`, `enableGenerateVideo`, `imageGenerationModel`, `videoGenerationModel`, and more from `CortexConvoUpdateRequest`. |
| `delete_data_room()` | `DELETE /cortex/conversation/{convo_id}` | Matches. |
| `check_file_upload_status()` | `GET /cortex/conversation/{convo_id}/attachment` | Matches (REST name: "Get Convo Attachments"). |
| `list_data_rooms()` | `GET /cortex/active-bot/{bot_id}/convo` | Matches. |
| `get_data_room()` | `GET /cortex/conversation/{convo_id}` | Matches. |
| `get_available_models()` | `GET /llm_model_usage` | Matches. |

---

## 3. Specific parameter & design inconsistencies

| Issue | Detail |
|---|---|
| **`change_model()` is misnamed** | Calls `PATCH /cortex/conversation/{convo_id}` ("Update Convo Detail") — does far more than changing a model. `CortexConvoUpdateRequest` supports ~20+ fields; SDK exposes only 14. |
| **`user_prompt()` missing v2 stream control** | REST v2 completions return a `message_id` with separate `resume`/`stop` endpoints (`POST .../resume`, `POST .../stop`). SDK cannot stop, resume, or check stream status. |
| **`upload_file()` bypasses `_make_request()`** | Uses `requests.post` directly, losing logging/error handling consistency. Also sends `session_id` as form data which may be undocumented. |
| **No `delete_attachment()` method** | REST has `DELETE /cortex/conversation/{convo_id}/attachment/{attachment_id}` and `DELETE /cortex/dataroom/{dataroom_id}/attachment/{attachment_id}`. |
| **No `delete_multiple_convos()`** | REST has `POST /cortex/conversation/delete-multiple-convos`. |
| **No message operations** | REST has `POST /cortex/message/list`, `GET /cortex/message/{message_id}`, `PATCH /cortex/message/edit-message`, `POST /cortex/message/feedback`, `PUT /cortex/message/rewind/{message_id}`, `DELETE /cortex/message/convo/{convo_id}`. |
| **No continue-generating / regen** | REST has `POST /cortex/completions/v2/continue-generating` and `POST /cortex/completions/v2/regen` (v1 equivalents too). |
| **`enable_generate_image` but no image model selection** | `change_model()` allows `enable_generate_image=True` but has no param for `imageGenerationModel` or `videoGenerationModel`, which the REST API supports. |

---

## 4. Summary

The `blockbrain_api` SDK (v0.1.3) covers approximately **10 out of 200+** REST API endpoints, focused on a narrow workflow:

1. Create a data room (conversation)
2. Upload a file
3. Send a prompt
4. Optionally change model / add context
5. Delete the data room

**Major missing capability areas**: knowledge base management, bot CRUD, workflows, agents, notes/insights, message history, sharing/collaboration, integrations (SharePoint/OneDrive/Gmail/Outlook), export, notifications, groups, speech, web search, and all admin/tenant management endpoints.
