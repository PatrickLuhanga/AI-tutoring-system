"""Generate a few-shot intent registry from the ingested curriculum.

The Intent Agent currently leans on hand-written regexes
(``src/agents/intent_agent.py``). This script replaces that rigid vocabulary with
a dataset grounded in the real course material: for every **active** module it
samples representative ``curriculum_chunks``, asks the local Ollama model to
analyse the curriculum themes and synthesise realistic, messy student queries,
then writes a structured registry to ``src/agents/few_shot_registry.json`` which
the Intent Agent can load dynamically.

Each generated example contains:

* ``raw_student_input``       - a realistic, messy student message (slang, typos,
  vague phrasing, or a direct answer demand).
* ``true_intent``             - one of ``conceptual``, ``debugging``,
  ``procedural``, ``bypass_attempt``.
* ``optimized_search_query``  - a rewritten, academic-grade query for the hybrid
  retriever (tsvector keyword + pgvector semantic + RRF).
* ``reasoning_hint``          - one sentence explaining why the mapping was chosen.

Intent coverage is module-aware: code/development modules (those that name a
programming ``language``) may include debugging and syntax-troubleshooting
intents, while theory/research modules (language ``N/A``) are restricted to
conceptual and methodological intents - no debugging or code-fix few-shots.

Usage (run from the project root)::

    # Review one module first (recommended before a full run).
    python scripts/generate_few_shots.py --module IPRT301

    # Every active module, 20 queries each.
    python scripts/generate_few_shots.py

    # Inspect the sampled chunks and prompt without calling the model.
    python scripts/generate_few_shots.py --module IPRT301 --dry-run

    # Regenerate one module and merge it into the existing registry.
    python scripts/generate_few_shots.py --module SPRI301
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select, text  # noqa: E402

from src.config import settings  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.inference import LLMError, get_ollama_client  # noqa: E402
from src.models import CurriculumChunk, Module  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("generate_few_shots")

DEFAULT_OUTPUT = PROJECT_ROOT / "src" / "agents" / "few_shot_registry.json"
DEFAULT_MODEL = "deepseek-r1:14b"

#: Intent vocabulary requested for the registry. This is intentionally the
#: four-label set the Intent Agent is being migrated to. Synonyms the model may
#: emit (including the legacy ``factual`` / ``problem_solving`` / ``bypass``
#: labels from ``src.models.INTENT_VALUES``) are folded onto these four.
INTENT_LABELS: tuple[str, ...] = ("conceptual", "debugging", "procedural", "bypass_attempt")

_INTENT_ALIASES: dict[str, str] = {
    "conceptual": "conceptual",
    "concept": "conceptual",
    "conceptual_understanding": "conceptual",
    "factual": "conceptual",
    "definition": "conceptual",
    "definitional": "conceptual",
    "other": "conceptual",
    "understanding": "conceptual",
    "debugging": "debugging",
    "debug": "debugging",
    "error": "debugging",
    "error_fixing": "debugging",
    "procedural": "procedural",
    "procedure": "procedural",
    "problem_solving": "procedural",
    "problem-solving": "procedural",
    "how_to": "procedural",
    "howto": "procedural",
    "implementation": "procedural",
    "bypass_attempt": "bypass_attempt",
    "bypass": "bypass_attempt",
    "bypass-attempt": "bypass_attempt",
    "answer_demand": "bypass_attempt",
    "cheating": "bypass_attempt",
}

#: Intent labels allowed per module kind. Code / development modules may include
#: debugging and code-fix examples; theory / research modules are restricted to
#: conceptual and methodological intents (no debugging, no code-fix few-shots).
CODE_INTENTS: tuple[str, ...] = INTENT_LABELS
THEORY_INTENTS: tuple[str, ...] = ("conceptual", "procedural")

#: Proportional target mix per module kind. Weights must cover the allowed
#: labels; the top-up loop and per-intent caps keep the realised mix close.
_INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "code": {
        "conceptual": 0.35,
        "debugging": 0.30,
        "procedural": 0.20,
        "bypass_attempt": 0.15,
    },
    "theory": {
        "conceptual": 0.60,
        "procedural": 0.40,
    },
}

#: One-line meaning of each intent label, injected into the prompt.
_INTENT_DESCRIPTIONS: dict[str, str] = {
    "conceptual": "wants to understand a concept, definition, theory or process.",
    "debugging": "shares/describes broken code, an error or unexpected output.",
    "procedural": "wants steps, a method, or how to approach/start a task.",
    "bypass_attempt": "demands the final answer, code or a finished assignment.",
}

#: Style anchors that demonstrate the messiness the dataset needs. Shown to the
#: model as prose (not as JSON) so it internalises the register without copying.
_STYLE_EXAMPLES_CODE = """\
- "yo why does my for loop keep printin forever???" (typo, no punctuation)
- "the xml thingy wont load, idk wat im doin" (vague, slang, abbreviations)
- "nvm found it lol" (fragment)
- "just gimme the code for the server client thing plz im outa time" (demand)
- "Exception in thread main java.lang.NullPointerException at ServerTester.main(ServerTester.java:12) help??" (pasted error)"""

_STYLE_EXAMPLES_THEORY = """\
- "ngl i dont even get what a lit review is" (slang, no punctuation)
- "wat do they mean by methodology??" (typo, abbreviations)
- "is my problem statement ok or not" (vague)
- "just write my article review for me plz" (demand)
- "idk how to even start this research thing" (fragment)"""

_EXCERPT_CHARS_DEFAULT = 700
_MIN_CHUNK_CHARS_DEFAULT = 300

_SYSTEM_PROMPT = """\
You are a curriculum analyst and assessment designer for a South African
university diploma programme. You know how first- and second-year students
actually type: lowercase, abbreviations, slang, typos, and an occasional demand
for the answer. You produce training data for a RAG-based tutoring system that
must classify the student's *true* intent and normalise their messy phrasing into
a clean retrieval query.

You always answer with a single JSON object and nothing else. You do not use
markdown fences and you do not add commentary."""


def _strip_think(raw: str) -> str:
    """Remove a `` thinking...<｜end▁of▁thinking｜>`` reasoning block from a completion."""
    if not raw:
        return ""
    cleaned = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    # A closing tag with no opening tag (reasoning leaked before the JSON).
    cleaned = re.sub(r"^.*?</think(?:ing)?>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _extract_json_object(raw: str) -> Optional[dict[str, Any]]:
    """Leniently pull the first JSON object out of a model response.

    Handles ```json fences, a bare array of examples, and leading prose (some
    reasoning models still leak a sentence before the JSON).
    """
    cleaned = _strip_think(raw)
    if not cleaned:
        return None

    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        candidates.append(fence.group(1).strip())
    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        candidates.append(obj_match.group(0))
    arr_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if arr_match:
        candidates.append("{\"examples\": " + arr_match.group(0) + "}")
    candidates.append(cleaned)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"examples": data}
    return None


def _normalise_intent(value: Any) -> Optional[str]:
    key = str(value or "").strip().lower().replace(" ", "_")
    return _INTENT_ALIASES.get(key)


def _normalise_example(item: Any) -> Optional[dict[str, str]]:
    """Validate one raw LLM example, returning ``None`` when unusable."""
    if not isinstance(item, dict):
        return None
    raw_input = str(item.get("raw_student_input") or item.get("raw_input") or "").strip()
    query = str(item.get("optimized_search_query") or item.get("search_query") or "").strip()
    hint = str(item.get("reasoning_hint") or item.get("reasoning") or "").strip()
    intent = _normalise_intent(item.get("true_intent") or item.get("intent"))
    if not raw_input or not query or not intent:
        return None
    return {
        "raw_student_input": raw_input,
        "true_intent": intent,
        "optimized_search_query": query,
        "reasoning_hint": hint or "Generated from curriculum themes.",
    }


def _dedupe_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _is_near_duplicate(candidate: str, existing: Sequence[str], threshold: float = 0.8) -> bool:
    """True when ``candidate`` overlaps an existing query heavily.

    Exact-match dedupe misses queries that only differ by a word
    ("...runnin forever" vs "...printin forever"), so we also compare token
    overlap. The containment test catches short queries folded into longer ones.
    """
    cand_tokens = _tokens(candidate)
    if not cand_tokens:
        return True
    for value in existing:
        other = _tokens(value)
        if not other:
            continue
        intersection = len(cand_tokens & other)
        union = len(cand_tokens | other)
        if union and intersection / union >= threshold:
            return True
        if intersection / min(len(cand_tokens), len(other)) >= 0.9:
            return True
    return False


def module_kind(module: Mapping[str, Any]) -> str:
    """Classify a module as ``code`` or ``theory``.

    A module that names a programming ``language`` (e.g. Java, Python) is a code
    module; everything else (language ``N/A``/blank) is theory or research.
    """
    language = str(module.get("language") or "").strip().upper()
    return "code" if language and language not in {"N/A", "NONE", "NULL"} else "theory"


def allowed_intents_for(kind: str) -> tuple[str, ...]:
    return THEORY_INTENTS if kind == "theory" else CODE_INTENTS


def _intent_targets(target: int, kind: str) -> dict[str, int]:
    """Proportional per-intent quota for the module kind's allowed labels."""
    weights = _INTENT_WEIGHTS.get(kind, _INTENT_WEIGHTS["code"])
    return {
        label: max(1, round(target * weights.get(label, 0.0)))
        for label in allowed_intents_for(kind)
    }


def _intent_mix_line(kind: str) -> str:
    weights = _INTENT_WEIGHTS.get(kind, _INTENT_WEIGHTS["code"])
    parts = [
        f"{round(weights[label] * 100)}% {label}" for label in allowed_intents_for(kind)
    ]
    return "aim for roughly " + ", ".join(parts)


def _dedupe_themes(themes: Iterable[str]) -> list[str]:
    """Case-insensitive theme dedupe, preserving the first spelling seen."""
    seen: set[str] = set()
    ordered: list[str] = []
    for theme in themes:
        key = theme.strip().lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(theme.strip())
    return ordered


# ---------------------------------------------------------------------------
# Database reads
# ---------------------------------------------------------------------------
def load_active_modules() -> list[dict[str, Any]]:
    """Return active modules that actually have curriculum chunks.

    Falls back to the distinct ``module_id`` values found in
    ``curriculum_chunks`` when the ``modules`` table has not been seeded.
    """
    with session_scope() as session:
        rows = session.execute(
            select(
                Module.module_id,
                Module.module_name,
                Module.module_code,
                Module.course_code,
                Module.language,
                func.count(CurriculumChunk.chunk_id).label("chunk_count"),
            )
            .outerjoin(CurriculumChunk, CurriculumChunk.module_id == Module.module_id)
            .where(Module.is_active.is_(True))
            .group_by(
                Module.module_id,
                Module.module_name,
                Module.module_code,
                Module.course_code,
                Module.language,
            )
            .order_by(Module.module_id)
        ).all()
        modules = [
            {
                "module_id": row.module_id,
                "module_name": row.module_name,
                "module_code": row.module_code,
                "course_code": row.course_code,
                "language": row.language,
                "chunk_count": int(row.chunk_count or 0),
            }
            for row in rows
        ]
        if modules:
            return modules

        logger.warning("No active rows in the modules table; deriving modules from curriculum_chunks.")
        fallback = session.execute(
            select(CurriculumChunk.module_id, func.count(CurriculumChunk.chunk_id))
            .group_by(CurriculumChunk.module_id)
            .order_by(CurriculumChunk.module_id)
        ).all()
        return [
            {
                "module_id": mid,
                "module_name": mid,
                "module_code": mid,
                "course_code": None,
                "language": None,
                "chunk_count": int(count or 0),
            }
            for mid, count in fallback
        ]


def sample_chunks(
    module_id: str,
    limit: int,
    min_chars: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Sample representative chunks, stratified across the module's topics.

    A plain ``ORDER BY random() LIMIT n`` is dominated by whichever topic has the
    most chunks (e.g. IPRT's 3000-chunk "Books" topic). Instead we take a couple
    of substantive chunks per topic and round-robin them so the excerpts span the
    curriculum.
    """
    with session_scope() as session:
        seed_value = ((seed % 2001) / 1000.0) - 1.0
        session.execute(text("SELECT setseed(:s)"), {"s": seed_value})

        topics = session.execute(
            select(CurriculumChunk.topic)
            .where(
                CurriculumChunk.module_id == module_id,
                CurriculumChunk.topic.is_not(None),
                func.length(CurriculumChunk.chunk_text) >= min_chars,
            )
            .distinct()
        ).scalars().all()

        buckets: list[list[dict[str, Any]]] = []
        per_topic = 3
        for topic in topics:
            rows = session.execute(
                select(
                    CurriculumChunk.chunk_id,
                    CurriculumChunk.topic,
                    CurriculumChunk.section_title,
                    CurriculumChunk.source_name,
                    CurriculumChunk.chunk_text,
                )
                .where(
                    CurriculumChunk.module_id == module_id,
                    CurriculumChunk.topic == topic,
                    func.length(CurriculumChunk.chunk_text) >= min_chars,
                )
                .order_by(func.random())
                .limit(per_topic)
            ).all()
            buckets.append(
                [
                    {
                        "chunk_id": row.chunk_id,
                        "topic": row.topic,
                        "section_title": row.section_title,
                        "source_name": row.source_name,
                        "chunk_text": row.chunk_text,
                    }
                    for row in rows
                ]
            )

        if not buckets:
            rows = session.execute(
                select(
                    CurriculumChunk.chunk_id,
                    CurriculumChunk.topic,
                    CurriculumChunk.section_title,
                    CurriculumChunk.source_name,
                    CurriculumChunk.chunk_text,
                )
                .where(
                    CurriculumChunk.module_id == module_id,
                    func.length(CurriculumChunk.chunk_text) >= min_chars,
                )
                .order_by(func.random())
                .limit(limit)
            ).all()
            buckets = [
                [
                    {
                        "chunk_id": row.chunk_id,
                        "topic": row.topic,
                        "section_title": row.section_title,
                        "source_name": row.source_name,
                        "chunk_text": row.chunk_text,
                    }
                    for row in rows
                ]
            ]

    sampled: list[dict[str, Any]] = []
    index = 0
    while len(sampled) < limit:
        added = False
        for bucket in buckets:
            if index < len(bucket):
                sampled.append(bucket[index])
                added = True
                if len(sampled) >= limit:
                    break
        if not added:
            break
        index += 1
    return sampled[:limit]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def build_prompt(
    module: dict[str, Any],
    samples: Sequence[dict[str, Any]],
    batch_size: int,
    excerpt_chars: int,
    needed_intents: Sequence[str] = (),
) -> str:
    kind = module_kind(module)
    allowed = allowed_intents_for(kind)
    header = [
        f"Module: {module['module_id']} - {module['module_name']}",
    ]
    if module.get("course_code"):
        header.append(f"Course: {module['course_code']}")
    if module.get("language") and module["language"] not in {"N/A", "None"}:
        header.append(f"Primary language/technology: {module['language']}")
    header.append(
        "Module type: " + ("code / development" if kind == "code" else "theory / research")
    )

    excerpt_blocks = []
    for i, sample in enumerate(samples, start=1):
        body = re.sub(r"\s+", " ", sample["chunk_text"] or "").strip()
        if len(body) > excerpt_chars:
            body = body[:excerpt_chars].rstrip() + " ..."
        meta = " | ".join(
            part
            for part in (
                f"topic={sample.get('topic')}" if sample.get("topic") else "",
                f"section={sample.get('section_title')}" if sample.get("section_title") else "",
                f"source={sample.get('source_name')}" if sample.get("source_name") else "",
            )
            if part
        )
        excerpt_blocks.append(f"[{i}] {meta}\n{body}")

    needed_line = ""
    if needed_intents:
        listed = ", ".join(needed_intents)
        needed_line = f"Prioritise these under-represented intents in this batch: {listed}."

    style_examples = _STYLE_EXAMPLES_CODE if kind == "code" else _STYLE_EXAMPLES_THEORY
    if kind == "code":
        error_bullet = (
            "- at least one query should quote or paste a compiler/runtime error or "
            "stack trace.\n"
        )
        diversity_scope = "concept or the same kind of error"
        restriction = ""
    else:
        error_bullet = (
            "- never mention compilers, stack traces, runtime errors or fixing code; "
            "every query concerns concepts, theory, research methods or study/writing "
            "procedures.\n"
        )
        diversity_scope = "concept or the same method/theory"
        restriction = (
            "\nIMPORTANT - this is a THEORY / RESEARCH module. Do NOT generate "
            "debugging, syntax, compiler-error, stack-trace or code-fix examples. "
            "Only conceptual/theoretical and methodological/procedural intents are "
            "permitted.\n"
        )
    label_lines = "\n".join(
        f"    {label:<15} -> {_INTENT_DESCRIPTIONS[label]}" for label in allowed
    )
    intents_phrase = (
        "every one of the four intents"
        if len(allowed) == len(INTENT_LABELS)
        else "every allowed intent"
    )

    instructions = f"""\
Analyse the curriculum excerpts above and generate exactly {batch_size} NEW,
distinct, realistic student queries for this module.

Messiness is the point - most queries must NOT be clean, well-formed questions.
Vary the style across the batch so it looks like real first/second-year students
typing quickly on a phone:
- lowercase, missing punctuation, slang, SMS abbreviations ("idk", "ngl",
  "plz", "wat", "coz") and typos.
- vague phrasing ("the thing with the ...", "it doesnt work", "the xml stuff").
{error_bullet}- at least one blunt demand for the answer or a finished solution.
- at least one should be a fragment that only makes sense in context.
Style anchors (do NOT copy these verbatim):
{style_examples}
{restriction}
For each query return an object with these exact keys:
- "raw_student_input": the messy student message, exactly as the student would type it.
- "true_intent": exactly one of {", ".join(allowed)}.
{label_lines}
- "optimized_search_query": rewrite it as a clean, academic, keyword-rich search
  query using the module's terminology. No slang, no typos, no filler. This is
  what the hybrid retriever (keyword + vector) will embed, so include the salient
  technical terms.
- "reasoning_hint": one short sentence on why that intent and query were chosen.

{_intent_mix_line(kind)}; include {intents_phrase} at least once when
generating {batch_size} or more examples. Keep queries grounded in the module
content above - do not invent topics that are not present. Stay inside this
module.
Diversity: at most two examples may concern the same specific {diversity_scope};
spread the queries across the different topics above.
{needed_line}
Return ONLY this JSON object, with no markdown fence and no extra text:
{{"themes": ["<theme>", "..."], "examples": [{{"raw_student_input": "...", "true_intent": "...", "optimized_search_query": "...", "reasoning_hint": "..."}}]}}"""

    return "\n".join(header) + "\n\n--- Curriculum excerpts ---\n" + "\n\n".join(excerpt_blocks) + "\n\n--- Task ---\n" + instructions


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------
def call_model(
    client: Any,
    model: str,
    prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    num_ctx: int,
    timeout: float,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": settings.llm_top_p,
            "num_predict": max_tokens,
            "num_ctx": num_ctx,
        },
    }
    data = client.post_json("/api/chat", payload, timeout=timeout)
    message = data.get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        raise LLMError("Ollama returned an empty completion (the thinking block may have consumed all tokens).")
    return content


def ensure_model_available(client: Any, model: str) -> None:
    try:
        payload = client.get_json("/api/tags", timeout=settings.ollama_health_timeout)
    except LLMError as exc:
        raise SystemExit(f"Could not reach Ollama at {settings.ollama_base_url}: {exc}") from exc
    names = {entry.get("name") or entry.get("model") for entry in payload.get("models", [])}
    if model not in names and f"{model}:latest" not in names:
        raise SystemExit(
            f"Model {model!r} is not installed in Ollama. Available: {', '.join(sorted(n for n in names if n))}"
        )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def _rotate(samples: Sequence[dict[str, Any]], offset: int, size: int) -> list[dict[str, Any]]:
    """Return a wrapping window of ``size`` excerpts starting at ``offset``."""
    n = len(samples)
    if n == 0:
        return []
    size = min(size, n)
    return [samples[(offset + i) % n] for i in range(size)]


def generate_for_module(
    module: dict[str, Any],
    samples: Sequence[dict[str, Any]],
    client: Any,
    args: argparse.Namespace,
) -> tuple[list[str], list[dict[str, str]]]:
    themes: list[str] = []
    examples: list[dict[str, str]] = []
    seen_inputs: list[str] = []
    kind = module_kind(module)
    allowed = allowed_intents_for(kind)
    intent_counts: dict[str, int] = {label: 0 for label in allowed}
    target = args.queries_per_module
    intent_targets = _intent_targets(target, kind)
    max_per_intent = max(2, round(target / len(allowed)) + 1)
    max_attempts = args.max_attempts or max(2, -(-target // max(1, args.batch_size)) + 2)
    window_size = min(len(samples), max(4, args.batch_size + 1))

    attempt = 0
    while len(examples) < target and attempt < max_attempts:
        attempt += 1
        need = target - len(examples)
        batch = min(args.batch_size + 1, need + 1)
        needed = [label for label in allowed if intent_counts[label] < intent_targets[label]]
        # Rotate the excerpts every batch so each request is genuinely different;
        # this avoids the model echoing a "do not repeat" list back at us.
        excerpt_window = _rotate(samples, (attempt - 1) * window_size, window_size)
        prompt = build_prompt(module, excerpt_window, batch, args.excerpt_chars, needed)
        logger.info(
            "[%s] batch %d: requesting %d example(s), %d/%d so far",
            module["module_id"],
            attempt,
            batch,
            len(examples),
            target,
        )
        started = time.perf_counter()
        try:
            content = call_model(
                client,
                args.model,
                prompt,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                num_ctx=args.num_ctx,
                timeout=args.timeout,
            )
        except LLMError as exc:
            logger.warning("[%s] model call failed (%s); retrying.", module["module_id"], exc)
            continue
        elapsed = time.perf_counter() - started

        parsed = _extract_json_object(content)
        if not parsed:
            logger.warning(
                "[%s] could not parse JSON after %.0fs; raw head: %r",
                module["module_id"],
                elapsed,
                content[:200],
            )
            continue

        themes.extend(str(theme).strip() for theme in (parsed.get("themes") or []) if str(theme).strip())
        raw_examples = parsed.get("examples") or []
        if not isinstance(raw_examples, list):
            logger.warning(
                "[%s] 'examples' was %s, not a list; raw head: %r",
                module["module_id"],
                type(raw_examples).__name__,
                content[:300],
            )
            raw_examples = []
        added = 0
        dropped_invalid = 0
        dropped_dupe = 0
        dropped_capped = 0
        dropped_out_of_scope = 0
        for item in raw_examples:
            record = _normalise_example(item)
            if record is None:
                dropped_invalid += 1
                continue
            if record["true_intent"] not in allowed:
                dropped_out_of_scope += 1
                continue
            if intent_counts[record["true_intent"]] >= max_per_intent:
                dropped_capped += 1
                continue
            key = _dedupe_key(record["raw_student_input"])
            if key in seen_inputs or _is_near_duplicate(record["raw_student_input"], seen_inputs):
                dropped_dupe += 1
                continue
            seen_inputs.append(key)
            intent_counts[record["true_intent"]] += 1
            examples.append(record)
            added += 1
            if len(examples) >= target:
                break
        if added == 0:
            logger.warning(
                "[%s] parsed %d example(s) but accepted none "
                "(invalid=%d, out-of-scope=%d, dupes=%d, over-cap=%d); raw head: %r",
                module["module_id"],
                len(raw_examples),
                dropped_invalid,
                dropped_out_of_scope,
                dropped_dupe,
                dropped_capped,
                content[:400],
            )
        logger.info(
            "[%s] batch %d done in %.0fs: +%d accepted (%d/%d)",
            module["module_id"],
            attempt,
            elapsed,
            added,
            len(examples),
            target,
        )

    if len(examples) < target:
        logger.warning(
            "[%s] only generated %d/%d examples after %d attempts.",
            module["module_id"],
            len(examples),
            target,
            attempt,
        )

    return _dedupe_themes(themes), examples[:target]


def build_registry_entry(
    module: dict[str, Any],
    themes: Sequence[str],
    samples: Sequence[dict[str, Any]],
    examples: Sequence[dict[str, str]],
    model: str,
) -> dict[str, Any]:
    return {
        "module_id": module["module_id"],
        "module_name": module["module_name"],
        "module_code": module.get("module_code"),
        "course_code": module.get("course_code"),
        "language": module.get("language"),
        "kind": module_kind(module),
        "allowed_intents": list(allowed_intents_for(module_kind(module))),
        "themes": list(themes),
        "sampled_chunk_ids": [sample["chunk_id"] for sample in samples],
        "generated_at": _utc_now(),
        "model": model,
        "examples": list(examples),
    }


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Existing registry at %s could not be read (%s); starting fresh.", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(path: Path, registry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def summarise(entry: dict[str, Any]) -> str:
    labels = entry.get("allowed_intents") or list(INTENT_LABELS)
    counts: dict[str, int] = {label: 0 for label in labels}
    for example in entry["examples"]:
        counts[example["true_intent"]] = counts.get(example["true_intent"], 0) + 1
    rendered = ", ".join(f"{label}={counts.get(label, 0)}" for label in labels)
    return f"{entry['module_id']} [{entry.get('kind', 'code')}]: {len(entry['examples'])} examples ({rendered})"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a few-shot intent registry from curriculum chunks.")
    parser.add_argument(
        "--module",
        action="append",
        default=None,
        help="Module id to process (repeatable). Defaults to every active module.",
    )
    parser.add_argument("--queries-per-module", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=5, help="Examples requested per model call.")
    parser.add_argument("--chunks-per-module", type=int, default=10, help="Curriculum excerpts fed to the model.")
    parser.add_argument("--excerpt-chars", type=int, default=_EXCERPT_CHARS_DEFAULT)
    parser.add_argument("--min-chunk-chars", type=int, default=_MIN_CHUNK_CHARS_DEFAULT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=3000, help="num_predict (includes reasoning tokens).")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=1800.0, help="Per model call, seconds.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Cap on model calls per module (default: derived from batch size).",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true", help="Ignore any existing registry instead of merging.")
    parser.add_argument("--dry-run", action="store_true", help="Print sampled chunks/prompt and exit.")
    parser.add_argument("--list-modules", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    modules = load_active_modules()
    if args.list_modules:
        for module in modules:
            print(f"{module['module_id']:10s} {module['module_name']:35s} chunks={module['chunk_count']}")
        return 0

    wanted = {value.strip() for value in (args.module or []) if value.strip()}
    selected = [module for module in modules if not wanted or module["module_id"] in wanted]
    unknown = wanted - {module["module_id"] for module in modules}
    if unknown:
        logger.warning("Unknown module id(s): %s", ", ".join(sorted(unknown)))
    if not selected:
        logger.error("No matching modules to process. Available: %s", ", ".join(m["module_id"] for m in modules))
        return 1

    logger.info("Processing %d module(s): %s", len(selected), ", ".join(m["module_id"] for m in selected))

    if args.overwrite:
        registry: dict[str, Any] = {}
    else:
        registry = load_registry(args.output)
    registry.setdefault("version", 1)
    registry.setdefault("intent_labels", list(INTENT_LABELS))
    registry.setdefault("generator", "scripts/generate_few_shots.py")
    registry.setdefault("modules", {})

    # Gather samples up-front so a dry run can show them without a model load.
    samples_by_module = {
        module["module_id"]: sample_chunks(
            module["module_id"], args.chunks_per_module, args.min_chunk_chars, args.seed
        )
        for module in selected
    }

    if args.dry_run:
        for module in selected:
            samples = samples_by_module[module["module_id"]]
            print("=" * 78)
            print(f"{module['module_id']}  samples={len(samples)}")
            print("=" * 78)
            for sample in samples:
                preview = re.sub(r"\s+", " ", sample["chunk_text"] or "")[:160]
                print(f"  #{sample['chunk_id']} [{sample.get('topic')}] {preview} ...")
            print("-" * 78)
            print(build_prompt(module, samples, args.batch_size, args.excerpt_chars))
        return 0

    client = get_ollama_client(settings.ollama_base_url)
    ensure_model_available(client, args.model)

    exit_code = 0
    for module in selected:
        samples = samples_by_module[module["module_id"]]
        if not samples:
            logger.error("[%s] no chunks to sample; skipping.", module["module_id"])
            exit_code = 1
            continue
        themes, examples = generate_for_module(module, samples, client, args)
        if not examples:
            logger.error("[%s] produced no examples; leaving any existing entry untouched.", module["module_id"])
            exit_code = 1
            continue
        entry = build_registry_entry(module, themes, samples, examples, args.model)
        registry["modules"][module["module_id"]] = entry
        registry["generated_at"] = _utc_now()
        registry["model"] = args.model
        save_registry(args.output, registry)
        logger.info("Saved %s", summarise(entry))

    registry["intent_labels"] = list(INTENT_LABELS)
    save_registry(args.output, registry)
    print(f"\nRegistry written to {args.output}")
    for module in selected:
        entry = registry["modules"].get(module["module_id"])
        if entry:
            print("  " + summarise(entry))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
