"""Local embedding generation.

The working embedding model is ``nomic-embed-text`` served by the local Ollama
runtime (architecture document, section 10.3). Nomic embeddings are
instruct-style: documents must be prefixed with ``search_document:`` and
queries with ``search_query:``, which is exactly what this module enforces -
callers can never forget a prefix because the *backend* owns it.

Two alternative backends remain available:

* ``sentence-transformers`` - a local HuggingFace model (e.g. the former
  default ``all-MiniLM-L6-v2``, 384-dim) loaded directly into the process.
* ``hash`` - a deterministic, dependency-free stub used only to smoke-test the
  ingestion plumbing. Vectors have the right shape but **no semantic meaning**;
  never use it for real retrieval.

The embedder fails loudly whenever a model's real output dimension disagrees
with ``EMBEDDING_DIM`` so a mismatched schema cannot go unnoticed.
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

#: Supported ``EMBEDDING_BACKEND`` values.
SUPPORTED_BACKENDS = ("ollama", "sentence-transformers", "hash")


class EmbeddingError(RuntimeError):
    """Raised when an embedding backend cannot be initialised or is misconfigured."""


class Embedder:
    """Lazy wrapper around an embedding backend.

    The underlying model is loaded on first use so importing this module (and
    therefore the CLI ``--help``) stays fast. Which prefix (document vs query)
    is applied depends on which method the caller uses:

    * :meth:`encode`     - document side (``search_document:``) - ingestion.
    * :meth:`encode_one` - query side (``search_query:``) - retrieval.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        dim: Optional[int] = None,
        device: Optional[str] = None,
        backend: Optional[str] = None,
        normalize: Optional[bool] = None,
        batch_size: Optional[int] = None,
        doc_prefix: Optional[str] = None,
        query_prefix: Optional[str] = None,
    ) -> None:
        self.model_name = model_name or settings.embedding_model_name
        self.dim = dim or settings.embedding_dim
        self.device = device or settings.embedding_device
        self.backend = (backend or settings.embedding_backend).lower()
        self.normalize = settings.embedding_normalize if normalize is None else normalize
        self.batch_size = batch_size or settings.embedding_batch_size
        self.doc_prefix = settings.embedding_doc_prefix if doc_prefix is None else doc_prefix
        self.query_prefix = settings.embedding_query_prefix if query_prefix is None else query_prefix
        self._model = None
        self._ollama_session = None

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
        if self.backend == "sentence-transformers":
            self._load_sentence_transformers()
            return
        if self.backend == "ollama":
            self._load_ollama()
            return
        raise EmbeddingError(
            f"Unknown EMBEDDING_BACKEND={self.backend!r}. "
            f"Use one of {SUPPORTED_BACKENDS}."
        )

    def _load_sentence_transformers(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - install-time issue
            raise EmbeddingError(
                "sentence-transformers is not installed. Run "
                "`pip install -r requirements.txt`, or set EMBEDDING_BACKEND=ollama "
                "to use nomic-embed-text through local Ollama."
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

    def _load_ollama(self) -> None:
        """Resolve the Ollama endpoint and warn if the model is not pulled.

        Loading stays lazy and non-fatal here: Ollama may be started after the
        process. The hard failure happens on the first encode, where a missing
        model or a dimension mismatch surfaces as an :class:`EmbeddingError`.
        """
        import requests

        self._ollama_session = requests.Session()
        self._model = "ollama"
        base_url = settings.ollama_base_url.rstrip("/")
        try:
            response = self._ollama_session.get(
                f"{base_url}/api/tags", timeout=settings.ollama_health_timeout
            )
            response.raise_for_status()
            # Tags carry a ":latest" suffix (or another explicit tag); compare
            # on the base name so `nomic-embed-text` matches `nomic-embed-text:latest`.
            pulled = {entry.get("name", "").split(":", 1)[0] for entry in response.json().get("models", [])}
        except Exception as exc:  # noqa: BLE001 - Ollama may simply not be running yet
            logger.warning(
                "Could not reach Ollama at %s while preparing embeddings: %s", base_url, exc
            )
            return
        if self.model_name.split(":", 1)[0] in pulled:
            logger.info("Embedding model %s is available in Ollama", self.model_name)
        else:
            logger.warning(
                "Embedding model %s was not found in Ollama. Pull it with: "
                "`ollama pull %s`",
                self.model_name,
                self.model_name,
            )

    # -- Encoding ------------------------------------------------------------
    def encode(
        self,
        texts: Sequence[str],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
        prefix: Optional[str] = None,
    ) -> np.ndarray:
        """Encode ``texts`` into an ``(n, dim)`` float32 array.

        ``prefix`` defaults to the document task prefix; pass
        ``self.query_prefix`` (or use :meth:`encode_one`) for queries.
        """
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        self._load()
        if self.backend == "hash":
            return self._hash_encode(texts)
        if self.backend == "ollama":
            return self._ollama_encode(
                texts,
                prefix=self.doc_prefix if prefix is None else prefix,
                batch_size=batch_size,
            )

        vectors = self._model.encode(
            list(texts),
            batch_size=batch_size or self.batch_size,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=show_progress,
        )
        return np.asarray(vectors, dtype=np.float32)

    def encode_one(self, text: str) -> list[float]:
        """Encode a single **query** (``search_query:`` prefix applied)."""
        return [float(value) for value in self.encode([text], prefix=self.query_prefix)[0]]

    # -- Ollama backend ------------------------------------------------------
    def _ollama_encode(
        self,
        texts: Sequence[str],
        *,
        prefix: str,
        batch_size: Optional[int] = None,
    ) -> np.ndarray:
        """Embed ``texts`` via Ollama's ``/api/embed`` with the task prefix."""
        import requests

        base_url = settings.ollama_base_url.rstrip("/")
        url = f"{base_url}/api/embed"
        size = batch_size or self.batch_size
        prompts = [f"{prefix}{text}" for text in texts]

        vectors: list[list[float]] = []
        for start in range(0, len(prompts), size):
            batch = prompts[start : start + size]
            body: dict = {"model": self.model_name, "input": batch}
            try:
                response = self._ollama_session.post(
                    url, json=body, timeout=settings.llm_request_timeout
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as exc:
                raise EmbeddingError(
                    f"Ollama embedding request failed at {url}: {exc}. "
                    f"Is Ollama running and is `{self.model_name}` pulled?"
                ) from exc
            except ValueError as exc:  # non-JSON body
                raise EmbeddingError(f"Ollama returned a non-JSON payload from {url}") from exc

            batch_vectors = data.get("embeddings")
            if not batch_vectors:
                # Older Ollama builds only accept the single-prompt shape.
                batch_vectors = [self._ollama_embed_single(url, prompt) for prompt in batch]
            vectors.extend(batch_vectors)

        array = np.asarray(vectors, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self.dim:
            raise EmbeddingError(
                f"Ollama model {self.model_name!r} produced {array.shape[1]}-dimensional "
                f"vectors but EMBEDDING_DIM={self.dim}. Update .env and recreate the schema."
            )
        if self.normalize:
            norms = np.linalg.norm(array, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            array = array / norms
        return array

    def _ollama_embed_single(self, url: str, prompt: str) -> list[float]:
        import requests

        try:
            response = self._ollama_session.post(
                url,
                json={"model": self.model_name, "prompt": prompt},
                timeout=settings.llm_request_timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise EmbeddingError(f"Ollama embedding request failed at {url}: {exc}") from exc
        embedding = data.get("embedding") or (data.get("embeddings") or [None])[0]
        if not embedding:
            raise EmbeddingError(f"Ollama returned no embedding for {self.model_name!r}.")
        return list(embedding)

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
