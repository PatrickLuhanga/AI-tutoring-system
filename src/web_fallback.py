"""Web-search fallback for turns the vector store cannot ground.

When a student's question retrieves *no* chunks from the module's own corpus,
the honest options are to answer from general knowledge (unverifiable, and the
architecture forbids presenting that as course material) or to search the web and
say so plainly. This module implements the second.

Design rules
------------
* **Whitelist first, general web second.** The lecturer-curated domains are
  tried first with a ``site:`` restriction; only if that yields nothing does a
  general web search run. A course-specific source beats an arbitrary one.
* **Never raise.** A network timeout, a rate-limit or a missing library must
  degrade to an empty result, so the workflow falls back to its existing honest
  reply rather than failing the student's turn.
* **Always attributable.** Every result carries its URL and domain so the tutor
  can cite it and the UI can flag it as *not* official course notes.

The consumer is :class:`src.agents.workflow.TutoringWorkflow`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .config import settings

logger = logging.getLogger(__name__)

#: The search backend was renamed duckduckgo-search -> ddgs. Import whichever is
#: present (a fresh install gets `ddgs`; older environments still have the old
#: name), and fail soft if neither is installed.
try:  # pragma: no cover - import-environment dependent
    from ddgs import DDGS  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        DDGS = None  # type: ignore[assignment]


@dataclass(slots=True)
class WebResult:
    """One web search hit, normalised for the tutor prompt and the UI."""

    title: str
    url: str
    domain: str
    snippet: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "snippet": self.snippet,
        }


@dataclass(slots=True)
class WebSearchOutcome:
    """The result of a two-pass search, with provenance for telemetry."""

    results: list[WebResult]
    #: True when the whitelist pass returned hits; False when the general pass
    #: was used (or nothing was found).
    from_whitelist: bool = False
    #: True when the general-web pass ran at all.
    used_general: bool = False
    error: Optional[str] = None

    @property
    def domains(self) -> list[str]:
        seen: list[str] = []
        for r in self.results:
            if r.domain not in seen:
                seen.append(r.domain)
        return seen

    def context_text(self) -> str:
        """Flatten hits into a labelled prompt block, like ``RetrievalResult``."""
        blocks: list[str] = []
        for index, r in enumerate(self.results, start=1):
            blocks.append(
                f"[Web {index}] {r.title} ({r.domain})\n{r.snippet}\nSource: {r.url}"
            )
        return "\n\n".join(blocks)


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _host_matches(host: str, domain: str) -> bool:
    """True when ``host`` is ``domain`` or a subdomain of it."""
    host = host.lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def _normalise(raw: dict, *, whitelist: list[str]) -> Optional[WebResult]:
    url = str(raw.get("href") or raw.get("url") or "").strip()
    if not url:
        return None
    domain = _domain_of(url)
    if not domain:
        return None
    # The general pass must not smuggle a whitelisted domain in silently; the
    # caller already knows which pass produced the hit via the outcome flags.
    title = str(raw.get("title") or domain).strip()
    snippet = str(raw.get("body") or raw.get("snippet") or "").strip()
    return WebResult(title=title, url=url, domain=domain, snippet=snippet)


def _run_search(query: str, *, max_results: int, timeout: int) -> list[dict]:
    """Call the search backend once. Raises nothing - returns [] on any failure."""
    if DDGS is None:
        logger.warning("No web-search backend installed (ddgs / duckduckgo-search).")
        return []
    try:
        # `ddgs` exposes a context manager; `timeout` is accepted by both.
        with DDGS(timeout=timeout) as client:  # type: ignore[call-arg]
            return list(client.text(query, max_results=max_results) or [])
    except TypeError:
        # Older signature without timeout: retry without it rather than crash.
        try:
            with DDGS() as client:  # type: ignore[call-arg]
                return list(client.text(query, max_results=max_results) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Web search failed for %r: %s", query[:60], exc)
            return []
    except Exception as exc:  # noqa: BLE001 - network/rate-limit/timeout
        logger.warning("Web search failed for %r: %s", query[:60], exc)
        return []


def search(
    query: str,
    *,
    whitelist: Optional[tuple[str, ...]] = None,
    max_results: Optional[int] = None,
    timeout: Optional[int] = None,
) -> WebSearchOutcome:
    """Two-pass web search: whitelisted domains first, then the general web.

    Returns a :class:`WebSearchOutcome`; never raises.
    """
    q = (query or "").strip()
    if not q:
        return WebSearchOutcome(results=[])

    domains = [d for d in (whitelist if whitelist is not None else settings.web_fallback_domains) if d]
    limit = max(1, int(max_results if max_results is not None else settings.web_fallback_max_results))
    tmo = max(1, int(timeout if timeout is not None else settings.web_fallback_timeout))

    # --- Pass 1: whitelisted domains -------------------------------------
    if domains:
        # One query per domain keeps each `site:` restriction valid; a single
        # OR-chained query is not honoured reliably by the backend.
        whitelist_hits: list[WebResult] = []
        per_domain = max(1, (limit + len(domains) - 1) // len(domains))
        for domain in domains:
            try:
                raw = _run_search(f"{q} site:{domain}", max_results=per_domain, timeout=tmo)
            except Exception as exc:  # noqa: BLE001 - a pass must never break the turn
                logger.warning("Whitelist search failed for %r: %s", domain, exc)
                raw = []
            for item in raw:
                result = _normalise(item, whitelist=domains)
                if result is None:
                    continue
                # Guard against the backend ignoring `site:`: only keep hits
                # genuinely on the requested domain.
                if _host_matches(result.domain, domain):
                    whitelist_hits.append(result)
            if len(whitelist_hits) >= limit:
                break
        if whitelist_hits:
            deduped = _dedupe(whitelist_hits)[:limit]
            logger.info("Web fallback: %d whitelist hit(s) for %r", len(deduped), q[:60])
            return WebSearchOutcome(results=deduped, from_whitelist=True)

    # --- Pass 2: general web ---------------------------------------------
    try:
        raw = _run_search(q, max_results=limit, timeout=tmo)
    except Exception as exc:  # noqa: BLE001 - a pass must never break the turn
        logger.warning("General web search failed for %r: %s", q[:60], exc)
        raw = []
    results = _dedupe([_normalise(item, whitelist=domains) for item in raw if _normalise(item, whitelist=domains)])
    logger.info(
        "Web fallback: %d general-web hit(s) for %r (whitelist missed)", len(results), q[:60]
    )
    return WebSearchOutcome(results=results[:limit], from_whitelist=False, used_general=True)


def _dedupe(results: list[Optional[WebResult]]) -> list[WebResult]:
    seen: set[str] = set()
    out: list[WebResult] = []
    for r in results:
        if r is None or r.url in seen:
            continue
        seen.add(r.url)
        out.append(r)
    return out
