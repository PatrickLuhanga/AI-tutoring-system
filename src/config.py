"""Central configuration for the Hybrid Data Tier.

Settings are read from the project-root ``.env`` file (see ``.env.example``)
and exposed through a single frozen :data:`settings` instance. This module also
owns the *module registry*: the mapping between the top-level folders inside
``academic content`` and the canonical ``module_id`` used to tag every vector
record (see architecture document, section 4.1 - module scope is applied as a
metadata filter before semantic search).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


# ---------------------------------------------------------------------------
# Typed environment helpers
# ---------------------------------------------------------------------------
def _str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else raw.strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Environment variable {name!r} must be an integer, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Environment variable {name!r} must be a float, got {raw!r}") from exc


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------
#: Canonical registry of the four modules in the current single-course scope.
#: The key is the top-level folder name inside ``academic content`` (matched
#: case-insensitively).
MODULE_REGISTRY: Dict[str, Dict[str, str]] = {
    "IPRT": {
        "module_id": "IPRT301",
        "module_code": "IPRT301",
        "module_name": "Internet Programming",
        "course_code": "DIP3",
        "language": "Java",
    },
    "PBDV": {
        "module_id": "PBDV301",
        "module_code": "PBDV301",
        "module_name": "Platform Based Development",
        "course_code": "DIP3",
        "language": "Python",
    },
    "RESK": {
        "module_id": "RESK301",
        "module_code": "RESK301",
        "module_name": "Research Skills",
        "course_code": "DIP3",
        "language": "N/A",
    },
    "SPRI": {
        "module_id": "SPRI301",
        "module_code": "SPRI301",
        "module_name": "Social and Professional Issues",
        "course_code": "DIP3",
        "language": "N/A",
    },
}

#: Extensions we can extract text from.
SUPPORTED_TEXT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".txt",
    ".md",
    ".markdown",
    ".java",
    ".py",
    ".ipynb",
    ".csv",
    ".html",
    ".htm",
    ".xml",
    ".json",
}

#: Extensions we deliberately ignore (media, archives, binaries, images).
SKIPPED_EXTENSIONS = {
    ".mp4",
    ".f4v",
    ".mov",
    ".avi",
    ".mkv",
    ".zip",
    ".rar",
    ".7z",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".doc",
    ".odp",
    ".ods",
    ".exe",
    ".dll",
    ".class",
    ".jar",
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _database_url() -> str:
    """Return the SQLAlchemy database URL.

    ``DATABASE_URL`` takes precedence. Otherwise the URL is assembled from the
    individual ``POSTGRES_*`` components so the same ``.env`` feeds both the
    application and ``docker-compose.yml``.
    """
    explicit = os.getenv("DATABASE_URL")
    if explicit and explicit.strip():
        return explicit.strip()

    user = _str("POSTGRES_USER", "tutor_admin")
    password = _str("POSTGRES_PASSWORD", "tutor_password")
    host = _str("POSTGRES_HOST", "localhost")
    port = _str("POSTGRES_PORT", "5432")
    database = _str("POSTGRES_DB", "ai_tutoring")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


@dataclass(frozen=True)
class Settings:
    """Immutable view over the environment configuration."""

    # Database
    database_url: str
    db_schema: str

    # Embeddings
    embedding_model_name: str
    embedding_dim: int
    embedding_device: str
    embedding_batch_size: int
    embedding_normalize: bool
    embedding_backend: str

    # Curriculum ingestion
    content_dir: Path
    chunk_size: int
    chunk_overlap: int

    # Vector indexes
    vector_index_type: str
    vector_index_lists: int
    vector_index_m: int
    vector_index_ef_construction: int

    # ------------------------------------------------------------------
    # Tier 2: Orchestration Tier
    # ------------------------------------------------------------------
    # Flask gateway
    flask_host: str
    flask_port: int
    flask_debug: bool

    # Authentication / authorization
    auth_mode: str
    admin_api_key: str
    #: Allow a placeholder ADMIN_API_KEY to authenticate. Local throwaway only.
    admin_allow_weak_key: bool
    #: Advance the Socratic stages on demonstrated attempts rather than on the
    #: number of turns elapsed. On a turn count, "ok thanks" five times reaches the
    #: Explanation stage and unlocks worked solutions without any work shown.
    scaffolding_evidence_based: bool
    #: Turns a student may stall at one stage before the ladder moves anyway, so a
    #: student who never attempts is not stranded by the rule above.
    scaffolding_max_stalled_turns: int
    enforce_enrollment: bool
    cors_allowed_origins: str

    # Dynamic LLM routing
    default_llm_provider: str
    ollama_base_url: str
    default_local_model: str
    default_cloud_provider: str
    default_cloud_base_url: str
    default_cloud_model: str
    llm_request_timeout: int
    llm_temperature: float
    llm_max_tokens: int
    llm_top_p: float
    llm_config_secret_key: str
    intent_use_llm: bool

    # Inference-tier fault isolation (Ollama circuit breaker)
    ollama_circuit_failure_threshold: int
    ollama_circuit_reset_timeout: float
    ollama_health_timeout: int

    # Ollama "thinking" models (qwen3, deepseek-r1, ...) emit a reasoning
    # channel before the answer. Left on, they burn the whole num_predict
    # budget reasoning and return an empty `content`, which the router reads
    # as a failure. See _generate_ollama in src/llm_router.py.
    ollama_think: bool

    # Retrieval
    retrieval_top_k: int
    retrieval_code_top_k: int
    #: Cosine-distance ceiling for a retrieved chunk to be trusted. A chunk further
    #: away than this is treated as "no relevant course material" rather than being
    #: pasted into the prompt as if it were relevant. 0 disables the floor.
    retrieval_max_distance: float
    #: Third-party material (currently ``source_category='books'``) is excluded by
    #: default so the tutor prefers the module's own faculty-approved material.
    retrieval_include_third_party: bool
    #: When the filtered search returns nothing, retry including third-party
    #: material rather than answering with an empty context.
    retrieval_third_party_fallback: bool
    #: pgvector HNSW behaviour when a metadata filter is applied. ``off`` silently
    #: returns fewer rows than ``top_k``; ``strict_order`` fixes that. Requires
    #: pgvector >= 0.8.
    hnsw_iterative_scan: str
    hnsw_ef_search: int
    #: Adaptive retrieval. When a first pass is this weak (nothing returned, or
    #: the closest chunk further away than this), the question is rewritten into
    #: alternative search phrasings and retrieved again. Costs one extra
    #: generation, and only on the turns that need it.
    retrieval_expand_on_weak: bool
    #: Cosine distance above which a first-pass result counts as weak.
    retrieval_weak_distance: float
    #: How many section titles to show the expander. The rewrite prompt is
    #: prefilled with them and this host is CPU-only: 60 titles measured 374 words
    #: of prefill and 72s per call, versus 42-74s for a whole tutor turn. Titles
    #: are ranked by how much they reveal about the module before the cap applies.
    retrieval_expand_titles: int

    # Guardrail
    guardrail_enabled: bool
    guardrail_max_code_lines: int
    guardrail_max_words: int
    guardrail_min_context_overlap: float
    guardrail_action: str

    # Module registry
    modules: Dict[str, Dict[str, str]] = field(default_factory=lambda: dict(MODULE_REGISTRY))

    # -- Convenience ---------------------------------------------------------
    def resolve_module(self, folder_name: str) -> Optional[Dict[str, str]]:
        """Map a top-level content folder (e.g. ``IPRT``) to its module record."""
        return self.modules.get(folder_name.strip().upper())

    def folder_for_module_id(self, module_id: str) -> Optional[str]:
        """Inverse of :meth:`resolve_module`: the content folder for a ``module_id``.

        The registry key and the module id are deliberately different strings -
        the folder on disk is ``IPRT`` while rows and URLs carry ``IPRT301``.
        Code that walks the corpus by ``module_id`` (the resource routes, the
        citation builder) has to translate, or it looks in a directory that does
        not exist.
        """
        target = (module_id or "").strip()
        for folder, record in self.modules.items():
            if record["module_id"] == target:
                return folder
        return None

    def resolve_any(self, identifier: str) -> Optional[tuple[str, Dict[str, str]]]:
        """Resolve either a registry key (``IPRT``) or a module id (``IPRT301``).

        Returns ``(folder, record)``. URLs and chat payloads carry the module id
        while the corpus is stored under the folder key, so anything crossing
        between the two should go through here rather than assuming one spelling.
        """
        name = (identifier or "").strip()
        if not name:
            return None
        record = self.modules.get(name.upper())
        if record is not None:
            return name.upper(), record
        for folder, candidate in self.modules.items():
            if candidate["module_id"] == name:
                return folder, candidate
        return None

    @property
    def supported_extensions(self) -> set[str]:
        return set(SUPPORTED_TEXT_EXTENSIONS)

    @property
    def skipped_extensions(self) -> set[str]:
        return set(SKIPPED_EXTENSIONS)

    def module_ids(self) -> Dict[str, str]:
        return {key: value["module_id"] for key, value in self.modules.items()}


def _build_settings() -> Settings:
    content_dir_raw = _str("ACADEMIC_CONTENT_DIR", "academic content")
    content_dir = Path(content_dir_raw)
    if not content_dir.is_absolute():
        content_dir = (PROJECT_ROOT / content_dir).resolve()

    return Settings(
        database_url=_database_url(),
        db_schema=_str("DB_SCHEMA", "public"),
        embedding_model_name=_str("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"),
        embedding_dim=_int("EMBEDDING_DIM", 384),
        embedding_device=_str("EMBEDDING_DEVICE", "cpu"),
        embedding_batch_size=_int("EMBEDDING_BATCH_SIZE", 64),
        embedding_normalize=_bool("EMBEDDING_NORMALIZE", True),
        embedding_backend=_str("EMBEDDING_BACKEND", "sentence-transformers").lower(),
        content_dir=content_dir,
        chunk_size=_int("CHUNK_SIZE", 1000),
        chunk_overlap=_int("CHUNK_OVERLAP", 150),
        vector_index_type=_str("VECTOR_INDEX_TYPE", "hnsw").lower(),
        vector_index_lists=_int("VECTOR_INDEX_LISTS", 100),
        vector_index_m=_int("VECTOR_INDEX_M", 16),
        vector_index_ef_construction=_int("VECTOR_INDEX_EF_CONSTRUCTION", 64),
        # Tier 2: Orchestration Tier
        flask_host=_str("FLASK_HOST", "127.0.0.1"),
        flask_port=_int("FLASK_PORT", 5000),
        flask_debug=_bool("FLASK_DEBUG", False),
        auth_mode=_str("AUTH_MODE", "dev").lower(),
        # Fails closed. A shipped default that works is a shipped default that is
        # never changed: it was "change-me-admin-key", .env.example handed out a
        # working key, and the local .env ended up using that example value
        # verbatim. Empty means require_admin() returns 503 and the admin API is
        # simply unavailable until someone sets a real key.
        admin_api_key=_str("ADMIN_API_KEY", ""),
        #: Escape hatch for a local throwaway only. Never set this on a host
        #: reachable by anyone else; it re-enables a known-placeholder key.
        admin_allow_weak_key=_bool("ADMIN_ALLOW_WEAK_KEY", False),
        scaffolding_evidence_based=_bool("SCAFFOLDING_EVIDENCE_BASED", True),
        scaffolding_max_stalled_turns=_int("SCAFFOLDING_MAX_STALLED_TURNS", 6),
        enforce_enrollment=_bool("ENFORCE_ENROLLMENT", False),
        cors_allowed_origins=_str("CORS_ALLOWED_ORIGINS", "*"),
        default_llm_provider=_str("LLM_PROVIDER", "local").lower(),
        ollama_base_url=_str("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        # Matches the model the evaluation numbers were collected with. This used
        # to default to qwen3:4b, a reasoning model: 196-591s per turn on a
        # CPU-only host and empty replies about 21% of the time. A machine with no
        # .env silently got the slow one.
        default_local_model=_str("DEFAULT_LOCAL_MODEL", "qwen2.5:3b-instruct"),
        default_cloud_provider=_str("DEFAULT_CLOUD_PROVIDER", "openai").lower(),
        default_cloud_base_url=_str("DEFAULT_CLOUD_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        default_cloud_model=_str("DEFAULT_CLOUD_MODEL", "gpt-4o-mini"),
        llm_request_timeout=_int("LLM_REQUEST_TIMEOUT", 120),
        llm_temperature=_float("LLM_TEMPERATURE", 0.4),
        llm_max_tokens=_int("LLM_MAX_TOKENS", 1024),
        llm_top_p=_float("LLM_TOP_P", 0.9),
        llm_config_secret_key=_str("LLM_CONFIG_SECRET_KEY", ""),
        intent_use_llm=_bool("INTENT_USE_LLM", True),
        ollama_circuit_failure_threshold=_int("OLLAMA_CIRCUIT_FAILURE_THRESHOLD", 3),
        ollama_circuit_reset_timeout=_float("OLLAMA_CIRCUIT_RESET_TIMEOUT", 30.0),
        ollama_health_timeout=_int("OLLAMA_HEALTH_TIMEOUT", 5),
        ollama_think=_bool("OLLAMA_THINK", False),
        retrieval_top_k=_int("RETRIEVAL_TOP_K", 3),
        retrieval_code_top_k=_int("RETRIEVAL_CODE_TOP_K", 3),
        retrieval_max_distance=_float("RETRIEVAL_MAX_DISTANCE", 0.75),
        retrieval_include_third_party=_bool("RETRIEVAL_INCLUDE_THIRD_PARTY", False),
        retrieval_third_party_fallback=_bool("RETRIEVAL_THIRD_PARTY_FALLBACK", True),
        hnsw_iterative_scan=_str("HNSW_ITERATIVE_SCAN", "strict_order").lower(),
        hnsw_ef_search=_int("HNSW_EF_SEARCH", 80),
        retrieval_expand_on_weak=_bool("RETRIEVAL_EXPAND_ON_WEAK", True),
        retrieval_weak_distance=_float("RETRIEVAL_WEAK_DISTANCE", 0.55),
        retrieval_expand_titles=_int("RETRIEVAL_EXPAND_TITLES", 20),
        guardrail_enabled=_bool("GUARDRAIL_ENABLED", True),
        guardrail_max_code_lines=_int("GUARDRAIL_MAX_CODE_LINES", 8),
        guardrail_max_words=_int("GUARDRAIL_MAX_WORDS", 400),
        guardrail_min_context_overlap=_float("GUARDRAIL_MIN_CONTEXT_OVERLAP", 0.08),
        guardrail_action=_str("GUARDRAIL_ACTION", "block").lower(),
    )


settings = _build_settings()
