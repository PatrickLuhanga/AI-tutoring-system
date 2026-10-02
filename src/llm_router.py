"""Dynamic LLM Router (Tier 2, section 10.2 of the architecture document).

The router is the single seam between the tutoring agents and whichever
generative model is currently active. Before **every** generation it reads the
active :class:`~src.models.LLMConfig` row from PostgreSQL and dispatches the
prompt to one of two backends:

* ``local``  - Ollama at ``ollama_base_url`` using the admin-selected model
  (``qwen3:4b`` by default).
* ``cloud``  - an OpenAI-compatible ``/chat/completions`` API or Anthropic's
  ``/messages`` API, authenticated with the encrypted, admin-provided key.

Nothing above the router knows or cares which backend served the request, which
is what keeps the inference model replaceable without redesigning the gateway,
scaffolding, RAG or guardrail layers.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import requests
from sqlalchemy import select, update
from sqlalchemy.exc import ProgrammingError

from .config import settings
from .db import engine, session_scope
from .inference import LLMError, get_ollama_client
from .models import CLOUD_PROVIDERS, LLM_PROVIDERS, LLMConfig
from .secrets_store import SecretStoreError, encrypt_secret, try_decrypt_secret

logger = logging.getLogger(__name__)

#: Re-exported for the many modules that historically imported it from here.
__all__ = [
    "LLMError",
    "LLMResponse",
    "LLMConfigService",
    "LLMRouter",
    "get_router",
    "strip_reasoning",
]


#: Reasoning models (qwen3, deepseek-r1, ...) can inline their chain of thought
#: into the reply body. A student must never see the tutor's internal monologue -
#: it is confusing, it leaks the hidden Scaffolding instructions, and it burns the
#: response budget. Anything up to a closing ``</think>`` is discarded.
_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking|reasoning|scratchpad)\b[^>]*>.*?"
    r"(?:</\1\s*>|(?=\Z))",
    re.IGNORECASE | re.DOTALL,
)
#: A stray closing tag with no opener (some runtimes split the channels oddly).
_ORPHAN_CLOSE_RE = re.compile(r"</(?:think|thinking|reasoning|scratchpad)\s*>", re.IGNORECASE)


def strip_reasoning(text: Optional[str]) -> str:
    """Remove any leaked chain-of-thought from a model reply.

    Best-effort and deliberately aggressive: a reply that loses a stray tag is far
    better than one that shows the student its own prompt scaffolding.
    """
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    if _ORPHAN_CLOSE_RE.search(cleaned):
        # A closing tag with no opener: everything before it was reasoning.
        cleaned = _ORPHAN_CLOSE_RE.split(cleaned, maxsplit=1)[-1]
    return cleaned.strip()


@dataclass(slots=True)
class LLMResponse:
    """A single model completion plus the provenance the router decided on."""

    text: str
    provider: str  # "local" | "cloud"
    model: str
    latency_ms: int
    backend: str = ""  # e.g. "ollama", "openai", "anthropic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "backend": self.backend,
            "latency_ms": self.latency_ms,
        }


# ---------------------------------------------------------------------------
# Configuration service - owns the llm_configs table
# ---------------------------------------------------------------------------
class LLMConfigService:
    """CRUD + secret handling for the single active LLM configuration.

    API keys are encrypted on write and never leave this class in plaintext
    except through :meth:`get_api_key`, which only the router calls.
    """

    def __init__(self) -> None:
        self._table_checked = False

    # -- Schema -------------------------------------------------------------
    def ensure_table(self) -> None:
        """Create ``llm_configs`` if a data-tier migration has not run yet.

        ``python -m src.setup_database`` creates the table from
        ``Base.metadata``. This guard keeps ``app.py`` startable on an existing
        database without forcing a destructive ``--drop``.
        """
        if self._table_checked:
            return
        try:
            LLMConfig.__table__.create(bind=engine, checkfirst=True)
            self._table_checked = True
        except ProgrammingError as exc:  # pragma: no cover - surface clearly
            raise LLMError(
                "Could not ensure the llm_configs table exists. Have you run "
                "`python -m src.setup_database`?"
            ) from exc

    # -- Reads --------------------------------------------------------------
    def get_active(self) -> Optional[LLMConfig]:
        """Return the active configuration row, falling back to the newest."""
        self.ensure_table()
        with session_scope() as session:
            active = session.execute(
                select(LLMConfig).where(LLMConfig.is_active.is_(True)).limit(1)
            ).scalar_one_or_none()
            if active is None:
                active = session.execute(
                    select(LLMConfig).order_by(LLMConfig.config_id.desc()).limit(1)
                ).scalar_one_or_none()
            return active

    def list_all(self) -> list[LLMConfig]:
        self.ensure_table()
        with session_scope() as session:
            return list(
                session.execute(select(LLMConfig).order_by(LLMConfig.config_id)).scalars()
            )

    def get_api_key(self, config: LLMConfig) -> Optional[str]:
        """Decrypt the stored cloud API key for the active config."""
        return try_decrypt_secret(config.api_key_encrypted)

    # -- Writes -------------------------------------------------------------
    def seed_default(self) -> LLMConfig:
        """Insert the default config if the table is empty (idempotent)."""
        self.ensure_table()
        existing = self.get_active()
        if existing is not None:
            return existing
        logger.info("Seeding default LLM configuration (provider=%s)", settings.default_llm_provider)
        return self.upsert_active(
            {
                "provider": settings.default_llm_provider,
                "ollama_base_url": settings.ollama_base_url,
                "local_model": settings.default_local_model,
                "cloud_provider": settings.default_cloud_provider,
                "cloud_base_url": settings.default_cloud_base_url,
                "cloud_model": settings.default_cloud_model,
                "temperature": settings.llm_temperature,
                "max_tokens": settings.llm_max_tokens,
                "top_p": settings.llm_top_p,
                "updated_by": "system-bootstrap",
            }
        )

    def upsert_active(self, payload: dict[str, Any]) -> LLMConfig:
        """Create or update the single active configuration row.

        Recognised keys: ``provider``, ``ollama_base_url``, ``local_model``,
        ``cloud_provider``, ``cloud_base_url``, ``cloud_model``, ``api_key``,
        ``clear_api_key``, ``temperature``, ``max_tokens``, ``top_p``,
        ``updated_by``.
        """
        self.ensure_table()
        current = self.get_active()

        provider = str(payload.get("provider") or (current.provider if current else "local")).lower()
        if provider not in LLM_PROVIDERS:
            raise ValueError(f"provider must be one of {LLM_PROVIDERS}, got {provider!r}")

        cloud_provider = str(
            payload.get("cloud_provider")
            or (current.cloud_provider if current else "openai")
        ).lower()
        if cloud_provider not in CLOUD_PROVIDERS:
            raise ValueError(
                f"cloud_provider must be one of {CLOUD_PROVIDERS}, got {cloud_provider!r}"
            )

        def pick(field: str, default: Any) -> Any:
            if field in payload and payload[field] is not None:
                return payload[field]
            if current is not None:
                return getattr(current, field)
            return default

        values: dict[str, Any] = {
            "provider": provider,
            "is_active": True,
            "ollama_base_url": str(pick("ollama_base_url", settings.ollama_base_url)).rstrip("/"),
            "local_model": pick("local_model", settings.default_local_model),
            "cloud_provider": cloud_provider,
            "cloud_base_url": str(pick("cloud_base_url", settings.default_cloud_base_url)).rstrip("/"),
            "cloud_model": pick("cloud_model", settings.default_cloud_model),
            "temperature": float(pick("temperature", settings.llm_temperature)),
            "max_tokens": int(pick("max_tokens", settings.llm_max_tokens)),
            "top_p": float(pick("top_p", settings.llm_top_p)),
            "updated_by": payload.get("updated_by") or (current.updated_by if current else None),
            "updated_at": dt.datetime.now(dt.timezone.utc),
        }

        # API key handling: only touch the key when the caller asked us to.
        api_key = payload.get("api_key")
        if payload.get("clear_api_key"):
            values["api_key_encrypted"] = None
            values["api_key_last4"] = None
        elif api_key:
            try:
                values["api_key_encrypted"] = encrypt_secret(str(api_key))
            except SecretStoreError as exc:
                raise ValueError(str(exc)) from exc
            values["api_key_last4"] = str(api_key)[-4:]

        with session_scope() as session:
            if current is None:
                record = LLMConfig(name="default", **values)
                session.add(record)
                session.flush()
                config_id = record.config_id
            else:
                session.execute(
                    update(LLMConfig).where(LLMConfig.config_id == current.config_id).values(**values)
                )
                config_id = current.config_id
        refreshed = self.get_active()
        assert refreshed is not None
        return refreshed

    # -- Serialisation ------------------------------------------------------
    def describe(self, config: LLMConfig) -> dict[str, Any]:
        """Public, secret-free view of a configuration row."""
        has_key = bool(config.api_key_encrypted)
        return {
            "config_id": config.config_id,
            "name": config.name,
            "is_active": config.is_active,
            "provider": config.provider,
            "local": {
                "base_url": config.ollama_base_url,
                "model": config.local_model,
            },
            "cloud": {
                "provider": config.cloud_provider,
                "base_url": config.cloud_base_url,
                "model": config.cloud_model,
                "has_api_key": has_key,
                "api_key_masked": f"....{config.api_key_last4}" if has_key else None,
            },
            "generation": {
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "top_p": config.top_p,
            },
            "effective": {
                "target": "ollama" if config.provider == "local" else config.cloud_provider,
                "model": config.local_model if config.provider == "local" else config.cloud_model,
            },
            "updated_by": config.updated_by,
            "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class LLMRouter:
    """Reads the active config before every generation and routes the prompt."""

    def __init__(self, config_service: Optional[LLMConfigService] = None) -> None:
        self.config_service = config_service or LLMConfigService()
        self._session = requests.Session()

    # -- Public API ---------------------------------------------------------
    def active_config(self) -> LLMConfig:
        """Read the active config from the DB; fall back to env defaults.

        Reading the row is the router's only data-tier dependency. If Postgres
        is unreachable this returns a transient, in-memory config built from
        ``.env`` so local generation can continue (graceful degradation) instead
        of failing the whole request.
        """
        try:
            config = self.config_service.get_active()
            if config is None:
                config = self.config_service.seed_default()
            return config
        except Exception as exc:  # noqa: BLE001 - Data Tier must not break generation
            logger.warning(
                "Could not read the active LLM configuration from the data tier "
                "(%s); using .env defaults for this request.",
                exc,
            )
            return self._fallback_config()

    @staticmethod
    def _fallback_config() -> LLMConfig:
        """An unpersisted :class:`LLMConfig` built entirely from ``.env``."""
        return LLMConfig(
            config_id=0,
            name="env-fallback",
            is_active=True,
            provider=settings.default_llm_provider,
            ollama_base_url=settings.ollama_base_url,
            local_model=settings.default_local_model,
            cloud_provider=settings.default_cloud_provider,
            cloud_base_url=settings.default_cloud_base_url,
            cloud_model=settings.default_cloud_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            top_p=settings.llm_top_p,
            extra={},
            updated_by="env-fallback",
        )

    def describe_active(self) -> dict[str, Any]:
        return self.config_service.describe(self.active_config())

    def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Route ``messages`` to the active backend and return the completion.

        ``messages`` is a list of ``{"role": "system"|"user"|"assistant",
        "content": "..."}`` dicts.
        """
        config = self.active_config()  # <- read from DB on every call
        if not messages:
            raise LLMError("Cannot generate from an empty message list.")

        temp = config.temperature if temperature is None else temperature
        tokens = config.max_tokens if max_tokens is None else max_tokens
        started = time.perf_counter()

        if config.provider == "local":
            text, backend = self._generate_ollama(config, messages, temp, tokens, json_mode)
        else:
            text, backend = self._generate_cloud(config, messages, temp, tokens)

        latency_ms = int((time.perf_counter() - started) * 1000)
        model = config.local_model if config.provider == "local" else config.cloud_model
        return LLMResponse(
            text=text.strip(),
            provider=config.provider,
            model=model,
            latency_ms=latency_ms,
            backend=backend,
        )

    def generate_text(self, prompt: str, *, system: Optional[str] = None, **kwargs: Any) -> LLMResponse:
        """Convenience wrapper for a single-turn prompt."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.generate(messages, **kwargs)

    # -- Ollama -------------------------------------------------------------
    def list_ollama_models(self, base_url: Optional[str] = None) -> list[dict[str, Any]]:
        """Ping Ollama's ``/api/tags`` and return the downloaded models.

        Used by ``GET /api/admin/ollama-models`` so the Admin Dashboard can
        populate a model dropdown instead of asking the admin to type a tag.
        The call is timeout-guarded and circuit-broken: if Ollama is down this
        raises :class:`LLMError` instead of hanging or crashing the gateway.
        """
        url = (base_url or settings.ollama_base_url).rstrip("/")
        client = get_ollama_client(url)
        payload = client.get_json("/api/tags", timeout=settings.ollama_health_timeout)

        models = []
        for entry in payload.get("models", []):
            size = entry.get("size")
            models.append(
                {
                    "name": entry.get("name") or entry.get("model"),
                    "model": entry.get("model"),
                    "size_bytes": size,
                    "size_gb": round(size / 1e9, 2) if isinstance(size, (int, float)) else None,
                    "modified_at": entry.get("modified_at"),
                    "family": (entry.get("details") or {}).get("family"),
                    "parameter_size": (entry.get("details") or {}).get("parameter_size"),
                }
            )
        return models

    def ollama_status(self, base_url: Optional[str] = None, *, probe: bool = False) -> dict[str, Any]:
        """Return circuit-breaker state, optionally with a live reachability probe."""
        client = get_ollama_client(base_url or settings.ollama_base_url)
        status = client.status().to_dict()
        if probe:
            try:
                client.probe()
                status["reachable"] = True
            except LLMError as exc:
                status["reachable"] = False
                status["error"] = str(exc)
        return status

    def _generate_ollama(
        self,
        config: LLMConfig,
        messages: Sequence[dict[str, str]],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> tuple[str, str]:
        url = f"{config.ollama_base_url.rstrip('/')}"
        client = get_ollama_client(config.ollama_base_url)
        body: dict[str, Any] = {
            "model": config.local_model,
            "messages": list(messages),
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": config.top_p,
                "num_predict": max_tokens,
                # Reasoning models (qwen3, deepseek-r1, ...) emit a `thinking`
                # channel that would otherwise be inlined into `content` and shown
                # to the student. This flag must sit INSIDE `options` on Ollama
                # 0.34.x - as a sibling of `options` it is silently ignored and the
                # reasoning leaks into the reply. Ignored by models with no
                # thinking channel.
                "think": settings.ollama_think,
            },
        }
        if json_mode:
            body["format"] = "json"

        data = client.post_json("/api/chat", body)
        text = strip_reasoning((data.get("message") or {}).get("content"))

        if not text:
            # A reasoning model can spend the entire num_predict budget on its
            # thinking channel and return nothing. That is not a transport failure
            # and retrying with the same budget just fails again, so escalate the
            # budget once and try again.
            retry_budget = max(max_tokens, int(settings.llm_max_tokens)) * 2
            logger.warning(
                "Ollama returned an empty response for %s (model=%s, num_predict=%d); "
                "retrying with num_predict=%d to leave room after the thinking channel.",
                url, config.local_model, max_tokens, retry_budget,
            )
            retry_body = dict(body)
            retry_body["options"] = {**body["options"], "num_predict": retry_budget}
            data = client.post_json("/api/chat", retry_body)
            text = strip_reasoning((data.get("message") or {}).get("content"))

        if not text:
            # Still nothing: the model is thinking longer than any sane budget.
            # Say so plainly rather than surfacing an empty hint to a student.
            raise LLMError(
                f"Ollama returned an empty response from {url} "
                f"(model={config.local_model}). {config.local_model} is a reasoning "
                f"model and used the whole token budget thinking. Either raise "
                f"LLM_MAX_TOKENS further, or switch to a non-reasoning model via "
                f"POST /api/admin/llm-config."
            )
        return text, "ollama"

    # -- Cloud --------------------------------------------------------------
    @staticmethod
    def _env_cloud_api_key(config: LLMConfig) -> Optional[str]:
        """Fall back to the process environment when no key is stored in the DB.

        Groq is the default cloud provider, so a deployment can supply
        ``GROQ_API_KEY`` in the environment (or ``.env``) and serve tutor turns
        without an admin first pasting the key into the dashboard. A key rotated
        through the admin API is still preferred, because the stored value is
        read first.
        """
        if config.cloud_provider == "groq":
            return settings.groq_api_key or None
        if config.cloud_provider in {"openai", "openai_compatible", "azure_openai"}:
            return os.getenv("OPENAI_API_KEY") or None
        return None

    def _generate_cloud(
        self,
        config: LLMConfig,
        messages: Sequence[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str]:
        api_key = self.config_service.get_api_key(config) or self._env_cloud_api_key(config)
        if not api_key:
            raise LLMError(
                "The active cloud provider has no API key configured. Set "
                "GROQ_API_KEY in the environment, or set one through "
                "POST /api/admin/llm-config."
            )

        if config.cloud_provider == "anthropic":
            text, backend = self._generate_anthropic(
                config, messages, api_key, temperature, max_tokens
            )
        else:
            text, backend = self._generate_openai(
                config, messages, api_key, temperature, max_tokens
            )
        # A reasoning model served through an OpenAI-compatible endpoint (e.g.
        # deepseek-r1) can inline its chain of thought the same way Ollama does.
        return strip_reasoning(text), backend

    def _generate_openai(
        self,
        config: LLMConfig,
        messages: Sequence[dict[str, str]],
        api_key: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str]:
        base = config.cloud_base_url.rstrip("/")
        if config.cloud_provider == "azure_openai":
            url = f"{base}/openai/deployments/{config.cloud_model}/chat/completions"
            api_version = (config.extra or {}).get("api_version", "2024-02-01")
            url = f"{url}?api-version={api_version}"
            headers = {"api-key": api_key, "Content-Type": "application/json"}
        else:
            url = f"{base}/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        body = {
            "model": config.cloud_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": config.top_p,
        }
        try:
            response = self._session.post(
                url, json=body, headers=headers, timeout=settings.llm_request_timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")
            raise LLMError(f"Cloud provider request to {url} failed: {exc} {detail[:300]}") from exc

        data = response.json() or {}
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected cloud response shape: {str(data)[:300]}") from exc
        if not text:
            raise LLMError("Cloud provider returned an empty response.")
        return text, config.cloud_provider

    def _generate_anthropic(
        self,
        config: LLMConfig,
        messages: Sequence[dict[str, str]],
        api_key: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str]:
        base = config.cloud_base_url.rstrip("/")
        url = f"{base}/messages"
        system_text = "\n\n".join(
            m["content"] for m in messages if m.get("role") == "system"
        )
        convo = [m for m in messages if m.get("role") in {"user", "assistant"}]
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": config.cloud_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": convo,
        }
        if system_text:
            body["system"] = system_text
        try:
            response = self._session.post(
                url, json=body, headers=headers, timeout=settings.llm_request_timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")
            raise LLMError(f"Anthropic request to {url} failed: {exc} {detail[:300]}") from exc

        data = response.json() or {}
        parts = [block.get("text", "") for block in data.get("content", []) if isinstance(block, dict)]
        text = "".join(parts)
        if not text:
            raise LLMError("Anthropic returned an empty response.")
        return text, "anthropic"


_ROUTER: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    """Return the process-wide router singleton."""
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = LLMRouter()
    return _ROUTER
