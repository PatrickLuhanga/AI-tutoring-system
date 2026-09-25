"""Inference Tier boundary (Tier 4).

The orchestration tier never talks to Ollama directly; it goes through this
package. That single seam is what lets a crashed inference runtime be isolated
(see :mod:`src.inference.ollama_client`).
"""

from .ollama_client import (
    INFERENCE_UNAVAILABLE_MESSAGE,
    CircuitBreaker,
    CircuitStatus,
    InferenceUnavailable,
    LLMError,
    OllamaClient,
    get_ollama_client,
)

__all__ = [
    "INFERENCE_UNAVAILABLE_MESSAGE",
    "CircuitBreaker",
    "CircuitStatus",
    "InferenceUnavailable",
    "LLMError",
    "OllamaClient",
    "get_ollama_client",
]
