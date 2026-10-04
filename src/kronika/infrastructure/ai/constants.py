"""Constants for server-side AI provider adapters."""

from __future__ import annotations

DEFAULT_PROVIDER_ID = "nvidia-nim"
DEFAULT_MODEL_ID = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
NVIDIA_CHAT_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
VERCEL_AI_GATEWAY_PROVIDER_ID = "vercel-ai-gateway"
VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID = "google/gemini-3.1-flash-lite"
VERCEL_AI_GATEWAY_CHAT_COMPLETIONS_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"

BUILTIN_PROVIDER_IDS = frozenset({DEFAULT_PROVIDER_ID, VERCEL_AI_GATEWAY_PROVIDER_ID})
SHARED_USER_AGENT = "framenest/0.1"

MAX_MODEL_ID_LENGTH = 120
MAX_REQUEST_BODY_BYTES = 24 * 1024 * 1024
MAX_RESPONSE_BODY_BYTES = 1 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 1024
TEMPERATURE = 0.2
TOP_K = 1
