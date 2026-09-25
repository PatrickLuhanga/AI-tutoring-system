"""Resilient client for the Inference Tier (Tier 4).

This module is the **only** place in the orchestration tier that speaks HTTP to
the local Ollama runtime. Keeping the socket call behind a dedicated client is
what lets the Flask gateway survive an inference outage (architecture section
5.1: "no single component should be responsible for everything").

Everything here is built around one idea: a crashed, hung or uninstalled Ollama
must degrade the API, never take the API down with it. Two mechanisms enforce
that:

* a per-request **timeout** so a hung model cannot pin a worker thread, and
* a small **circuit breaker** so that once Ollama has failed repeatedly the
  gateway stops attempting to connect and fails fast instead of stacking up
  timeouts.

When the breaker is open, or a call fails, :class:`InferenceUnavailable` is
raised. The Flask layer catches that (it is an :class:`LLMError`) and returns the
clean JSON payload in :data:`INFERENCE_UNAVAILABLE_MESSAGE` - the UI keeps
working and shows the student a friendly message.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from ..config import settings

logger = logging.getLogger(__name__)


#: The single, user-facing message returned whenever the inference tier is down.
INFERENCE_UNAVAILABLE_MESSAGE = (
    "The AI inference engine is temporarily unavailable. "
    "Please check your local Ollama runtime."
)


class LLMError(RuntimeError):
    """Raised when an inference backend is unreachable or misconfigured.

    Defined here (not in the router) so that both the inference client and the
    router can depend on it without a circular import. It is re-exported from
    :mod:`src.llm_router` for backwards compatibility.
    """


class InferenceUnavailable(LLMError):
    """Raised when Ollama is down, slow, or the circuit breaker is open.

    ``str(exc)`` is always the safe, user-facing message; the technical reason
    is kept in :attr:`detail` for logs only.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(INFERENCE_UNAVAILABLE_MESSAGE)
        self.detail = detail

    def __str__(self) -> str:  # keep logs informative without leaking to the UI
        return f"{INFERENCE_UNAVAILABLE_MESSAGE} ({self.detail})" if self.detail else INFERENCE_UNAVAILABLE_MESSAGE


@dataclass(slots=True)
class CircuitStatus:
    """A snapshot of the breaker for health checks and admin dashboards."""

    state: str  # "closed" | "open" | "half_open"
    failures: int
    retry_in: float  # seconds until the next probe is allowed (0 when closed)

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "failures": self.failures, "retry_in": self.retry_in}


class CircuitBreaker:
    """Thread-safe failure-counting circuit breaker.

    States::

        closed     -> all calls allowed
        open       -> calls rejected until ``reset_timeout`` elapses
        half_open  -> one trial call allowed; success closes, failure re-opens

    Every method takes the lock, so a threaded Flask dev server or a gunicorn
    worker pool can share one breaker safely.
    """

    def __init__(self, failure_threshold: int, reset_timeout: float) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_timeout = max(0.0, float(reset_timeout))
        self._lock = threading.Lock()
        self._failures = 0
        self._state = "closed"
        self._opened_at: Optional[float] = None

    # -- Gate ---------------------------------------------------------------
    def allow_request(self) -> bool:
        """Return whether a call may proceed, advancing open -> half-open."""
        with self._lock:
            self._advance_to_half_open_locked()
            return self._state != "open"

    # -- Outcome ------------------------------------------------------------
    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"
            self._opened_at = None

    # -- Introspection ------------------------------------------------------
    def status(self) -> CircuitStatus:
        with self._lock:
            self._advance_to_half_open_locked()
            retry_in = 0.0
            if self._state == "open" and self._opened_at is not None:
                retry_in = max(0.0, self.reset_timeout - (time.monotonic() - self._opened_at))
            return CircuitStatus(self._state, self._failures, round(retry_in, 2))

    # -- Internals ----------------------------------------------------------
    def _advance_to_half_open_locked(self) -> None:
        if self._state == "open" and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.reset_timeout:
                self._state = "half_open"
                logger.info("Ollama circuit breaker moved to half-open; probing once.")


class OllamaClient:
    """Timeout-guarded, circuit-broken HTTP client for one Ollama endpoint."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: Optional[float] = None,
        breaker: Optional[CircuitBreaker] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.timeout = float(timeout if timeout is not None else settings.llm_request_timeout)
        self.breaker = breaker or CircuitBreaker(
            settings.ollama_circuit_failure_threshold,
            settings.ollama_circuit_reset_timeout,
        )
        self._session = session or requests.Session()

    # -- Public API ---------------------------------------------------------
    def get_json(self, path: str, *, timeout: Optional[float] = None) -> dict[str, Any]:
        return self._request("GET", path, timeout=timeout)

    def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, json_body=payload, timeout=timeout)

    def probe(self, timeout: Optional[float] = None) -> dict[str, Any]:
        """Lightweight liveness probe that also drives the breaker."""
        return self.get_json("/api/tags", timeout=timeout or settings.ollama_health_timeout)

    def status(self) -> CircuitStatus:
        return self.breaker.status()

    # -- Internals ----------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        effective_timeout = float(timeout if timeout is not None else self.timeout)

        if not self.breaker.allow_request():
            status = self.breaker.status()
            logger.warning(
                "Ollama circuit is open; rejecting %s %s (retry in %.1fs).",
                method,
                url,
                status.retry_in,
            )
            raise InferenceUnavailable(
                detail=f"circuit {status.state}; retry in {status.retry_in}s"
            )

        try:
            response = self._session.request(
                method, url, json=json_body, timeout=effective_timeout
            )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout as exc:
            self.breaker.record_failure()
            logger.warning("Ollama timed out after %.1fs at %s.", effective_timeout, url)
            raise InferenceUnavailable(detail=f"timeout after {effective_timeout}s") from exc
        except requests.ConnectionError as exc:
            self.breaker.record_failure()
            logger.warning("Ollama is unreachable at %s: %s", url, exc)
            raise InferenceUnavailable(detail=f"connection refused at {url}") from exc
        except requests.HTTPError as exc:
            # Ollama answered (so the service is alive) but rejected the call,
            # e.g. an unknown model. Do NOT trip the breaker for this.
            response = exc.response
            body = (response.text or "")[:200] if response is not None else ""
            logger.warning("Ollama returned HTTP %s for %s: %s", getattr(response, "status_code", "?"), url, body)
            raise InferenceUnavailable(detail=f"HTTP error: {body}") from exc
        except requests.RequestException as exc:
            self.breaker.record_failure()
            logger.warning("Ollama request to %s failed: %s", url, exc)
            raise InferenceUnavailable(detail=str(exc)) from exc
        except ValueError as exc:  # non-JSON body
            self.breaker.record_failure()
            raise InferenceUnavailable(detail=f"invalid JSON from {url}") from exc

        self.breaker.record_success()
        if not isinstance(data, dict):
            raise InferenceUnavailable(detail=f"unexpected payload from {url}")
        return data


_CLIENTS: dict[str, OllamaClient] = {}
_CLIENTS_LOCK = threading.Lock()


def get_ollama_client(base_url: Optional[str] = None) -> OllamaClient:
    """Return a process-wide client (with its breaker) for ``base_url``.

    Caching per base URL means the breaker's state survives across requests,
    which is what makes it an actual circuit breaker rather than a per-request
    counter, while still respecting an admin changing the Ollama endpoint.
    """
    key = (base_url or settings.ollama_base_url).rstrip("/")
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = OllamaClient(key)
            _CLIENTS[key] = client
        return client
