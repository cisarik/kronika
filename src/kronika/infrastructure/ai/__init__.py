"""Infrastructure adapters for AI-assisted media suggestion prototypes."""

from kronika.infrastructure.ai.nvidia_nim import NvidiaNimMediaSuggestionProvider
from kronika.infrastructure.ai.vercel_gateway import VercelAiGatewayMediaSuggestionProvider

__all__ = ["NvidiaNimMediaSuggestionProvider", "VercelAiGatewayMediaSuggestionProvider"]
