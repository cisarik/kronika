"""Vercel AI Gateway media suggestion adapter."""

from __future__ import annotations

from typing import Any

from kronika.application.media_suggestion import MediaSuggestionRequest
from kronika.infrastructure.ai.constants import (
    VERCEL_AI_GATEWAY_CHAT_COMPLETIONS_URL,
    VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
    VERCEL_AI_GATEWAY_PROVIDER_ID,
)
from kronika.infrastructure.ai.credentials import VercelAiGatewayCredential
from kronika.infrastructure.ai.image_derivative import VlmImageDerivativeEncoder
from kronika.infrastructure.ai.nvidia_nim import JsonTransport
from kronika.infrastructure.ai.openai_chat_completions import (
    OpenAiChatCompletionsMediaSuggestionProvider,
    build_chat_completions_connection_test_body,
    build_chat_completions_suggestion_body,
)

VERCEL_AI_GATEWAY_BASE_URL = VERCEL_AI_GATEWAY_CHAT_COMPLETIONS_URL.removesuffix("/chat/completions")


def build_vercel_gateway_request_body(
    request: MediaSuggestionRequest,
    *,
    model_id: str,
    image_encoder: VlmImageDerivativeEncoder | None = None,
) -> dict[str, Any]:
    """Build an OpenAI-compatible Vercel AI Gateway request body."""
    return build_chat_completions_suggestion_body(
        request,
        model_id=model_id,
        image_encoder=image_encoder,
    )


def build_vercel_gateway_connection_test_body(*, model_id: str) -> dict[str, Any]:
    """Build a text-only Vercel AI Gateway connection test request."""
    return build_chat_completions_connection_test_body(model_id=model_id)


class VercelAiGatewayMediaSuggestionProvider(OpenAiChatCompletionsMediaSuggestionProvider):
    """Vercel AI Gateway adapter over the generic chat-completions path."""

    def __init__(
        self,
        credential: VercelAiGatewayCredential,
        transport: JsonTransport | None = None,
        *,
        model_id: str = VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        provider_id: str = VERCEL_AI_GATEWAY_PROVIDER_ID,
        image_encoder: VlmImageDerivativeEncoder | None = None,
    ) -> None:
        super().__init__(
            credential,
            base_url=VERCEL_AI_GATEWAY_BASE_URL,
            provider_id=provider_id,
            model_id=model_id,
            transport=transport,
            image_encoder=image_encoder,
        )

    def __repr__(self) -> str:
        return "VercelAiGatewayMediaSuggestionProvider(<redacted>)"
