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
    ".java",
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

    # Retrieval
    retrieval_top_k: int
    retrieval_code_top_k: int

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
        admin_api_key=_str("ADMIN_API_KEY", "change-me-admin-key"),
        enforce_enrollment=_bool("ENFORCE_ENROLLMENT", False),
        cors_allowed_origins=_str("CORS_ALLOWED_ORIGINS", "*"),
        default_llm_provider=_str("LLM_PROVIDER", "local").lower(),
        ollama_base_url=_str("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        default_local_model=_str("DEFAULT_LOCAL_MODEL", "qwen3:4b"),
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
        retrieval_top_k=_int("RETRIEVAL_TOP_K", 3),
        retrieval_code_top_k=_int("RETRIEVAL_CODE_TOP_K", 3),
        guardrail_enabled=_bool("GUARDRAIL_ENABLED", True),
        guardrail_max_code_lines=_int("GUARDRAIL_MAX_CODE_LINES", 8),
        guardrail_max_words=_int("GUARDRAIL_MAX_WORDS", 400),
        guardrail_min_context_overlap=_float("GUARDRAIL_MIN_CONTEXT_OVERLAP", 0.08),
        guardrail_action=_str("GUARDRAIL_ACTION", "block").lower(),
    )


settings = _build_settings()
