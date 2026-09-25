"""Local embedding generation.

The working embedding model is ``all-MiniLM-L6-v2`` loaded through
SentenceTransformers. It is small, free, CPU-friendly and produces
384-dimensional vectors, so it can run on a developer laptop without competing
with the (future) generative model for resources.

An offline ``hash`` backend is included so the ingestion *plumbing* can be
smoke-tested on machines without the ML stack installed. It is deterministic
but carries no semantic meaning - it must never be used for real retrieval.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional, Sequence

import numpy as np

from .config import settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class EmbeddingError(RuntimeError):
    """Raised when an embedding backend cannot be initialised."""


class Embedder:
    """Lazy wrapper around an embedding backend.

    The underlying model is loaded on first use so importing this module (and
    therefore the CLI ``--help``) stays fast.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        dim: Optional[int] = None,
        device: Optional[str] = None,
        backend: Optional[str] = None,
        normalize: Optional[bool] = None,
        batch_size: Optional[int] = None,
    ) -> None:
        self.model_name = model_name or settings.embedding_model_name
        self.dim = dim or settings.embedding_dim
        self.device = device or settings.embedding_device
        self.backend = (backend or settings.embedding_backend).lower()
        self.normalize = settings.embedding_normalize if normalize is None else normalize
        self.batch_size = batch_size or settings.embedding_batch_size
        self._model = None

    # -- Model loading -------------------------------------------------------
    def _load(self) -> None:
        if self._model is not None:
            return
        if self.backend == "hash":
            logger.warning(
                "Embedding backend 'hash' is active: vectors are deterministic "
                "stubs with no semantic meaning. Use only for pipeline smoke tests."
            )
            self._model = "hash"
            return
        if self.backend != "sentence-transformers":
            raise EmbeddingError(
                f"Unknown EMBEDDING_BACKEND={self.backend!r}. "
                "Use 'sentence-transformers' or 'hash'."
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - install-time issue
            raise EmbeddingError(
                "sentence-transformers is not installed. Run "
                "`pip install -r requirements.txt`, or set EMBEDDING_BACKEND=hash "
                "to smoke-test the pipeline without the ML stack."
            ) from exc

        logger.info("Loading embedding model %s (device=%s)", self.model_name, self.device)
        model = SentenceTransformer(self.model_name, device=self.device)
        actual_dim = model.get_sentence_embedding_dimension()
        if actual_dim != self.dim:
            raise EmbeddingError(
                f"EMBEDDING_DIM={self.dim} does not match the dimension reported by "
                f"{self.model_name} ({actual_dim}). Update .env and recreate the schema."
            )
        self._model = model

    # -- Encoding ------------------------------------------------------------
    def encode(
        self,
        texts: Sequence[str],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> np.ndarray:
        """Encode ``texts`` into an ``(n, dim)`` float32 array."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        self._load()
        if self.backend == "hash":
            return self._hash_encode(texts)

        vectors = self._model.encode(
            list(texts),
            batch_size=batch_size or self.batch_size,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=show_progress,
        )
        return np.asarray(vectors, dtype=np.float32)

    def encode_one(self, text: str) -> list[float]:
        return [float(value) for value in self.encode([text])[0]]

    # -- Offline stub --------------------------------------------------------
    def _hash_encode(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = _TOKEN_RE.findall((text or "").lower()) or [text or ""]
            for token in tokens:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                out[row, index] += sign
        if self.normalize:
            norms = np.linalg.norm(out, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            out = out / norms
        return out


_EMBEDDER: Optional[Embedder] = None


def get_embedder() -> Embedder:
    """Return the process-wide embedder singleton."""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder()
    return _EMBEDDER
