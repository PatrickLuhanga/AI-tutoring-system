"""Generation-side evaluation (paper sections 3.4 and 5).

The retrieval harness scores whether the *right material* was found. This one
scores what the tutor then *did* with it, which is where the paper's headline
claims live: reduced factual error (grounding) and reduced student
over-reliance (no leaked solutions, refusals honoured).

Metrics are deliberately deterministic rather than LLM-judged wherever possible,
so a run is reproducible, free, and not biased by having the model under test
also grade itself. Each is computed from the reply text and the retrieved chunks:

``groundedness``       mean cosine similarity between each reply sentence and
                       its nearest retrieved chunk. A reply built from the
                       retrieved material scores high; a reply invented from
                       parametric memory scores low. This is a direct proxy for
                       the 28% factual-error rate the review reports.
``answer_leak``        longest verbatim word n-gram shared with any chunk flagged
                       ``is_answer``, plus the largest code block emitted. A
                       Socratic tutor should share ~nothing with a worked
                       solution before the Explanation stage.
``socratic_compliance``for scaffold-route turns, does the reply actually ask the
                       student something? Directly tests scaffolding collapse.
``refusal_honoured``   for bypass turns, did it decline rather than comply?

``--judge`` optionally adds an LLM rubric on top. Use it for commentary, not for
the headline numbers: with no cloud key available the judge is the same local
model that produced the answers, which is a real methodological weakness.

Ablations reproduce the comparison the paper promises against a standalone LLM:

    --no-rag            retrieval disabled (the "standalone LLM" baseline)
    --no-guardrail      the Guardrail Agent approves everything
    --no-scaffolding    every turn takes the direct-answer route

Usage::

    python -m eval.run_generation_eval --limit 8
    python -m eval.run_generation_eval --limit 8 --no-rag
    python -m eval.run_generation_eval --limit 8 --out results/hybrid.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents import TutoringWorkflow  # noqa: E402
from src.agents.intent_agent import Intent  # noqa: E402
from src.config import settings  # noqa: E402
from src.prompts import INTENT_ROUTES, ROUTE_DIRECT  # noqa: E402
from src.retriever import RetrievalPolicy, RetrievalResult, get_retriever  # noqa: E402
from eval.run_retrieval_eval import Row, load_rows  # noqa: E402

TEST_SET = PROJECT_ROOT / "eval" / "test_set.csv"

#: A reply sentence is a "unit" for groundedness. Split conservatively so a code
#: block is not chopped into meaningless fragments.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_CODE_FENCE_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
_QUESTION_RE = re.compile(r"\?")
_REFUSAL_RE = re.compile(
    r"(?i)\b(can'?t|cannot|won'?t write|not able to|instead of writing|"
    r"help you (?:build|work|work through)|step by step|let'?s (?:start|build|work)|"
    r"what (?:part|do you)|which part|guide you|walk you through)\b"
)


# ---------------------------------------------------------------------------
# Ablation shims
# ---------------------------------------------------------------------------
class NoRagRetriever:
    """Stands in for the Retriever and returns nothing: the standalone-LLM baseline."""

    def retrieve(self, query, module_id, *, include_patterns=True, policy=None):
        return RetrievalResult(query=query, module_id=module_id)


class PermitAllGuardrail:
    """Stands in for the Guardrail Agent and approves every draft."""

    def __init__(self) -> None:
        from src.agents.guardrail import GuardrailResult

        self._cls = GuardrailResult

    def audit(self, text, context, question, allow_direct=False):
        return self._cls(approved_text=text, flagged=False, flags=[])


class AlwaysDirectIntent:
    """Forces every turn onto the direct-answer route: no Socratic scaffolding."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def classify(self, message, module_name=None, history=None):
        intent: Intent = self._inner.classify(message, module_name, history)
        return Intent(
            label="factual",
            confidence=intent.confidence,
            rationale="ablation: --no-scaffolding",
            source="ablation",
            route=ROUTE_DIRECT,
        )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _embedder():
    from src.embeddings import get_embedder

    return get_embedder()


def groundedness(reply: str, chunk_texts: list[str]) -> Optional[float]:
    """Mean over reply sentences of the best cosine similarity to a chunk.

    ``None`` when nothing was retrieved, since there is nothing to ground in.
    """
    if not chunk_texts or not reply.strip():
        return None
    sentences = [s.strip() for s in _SENTENCE_RE.split(reply) if len(s.strip()) > 15]
    if not sentences:
        return None
    import numpy as np

    emb = _embedder()
    reply_vecs = emb.encode(sentences)
    chunk_vecs = emb.encode(chunk_texts)
    # Vectors are L2-normalised by the embedder, so a dot product is cosine.
    sims = reply_vecs @ chunk_vecs.T
    return float(sims.max(axis=1).mean())


def answer_leak(reply: str, answer_chunk_texts: list[str], *, n: int = 8) -> tuple[int, int]:
    """Return ``(longest_shared_ngram_words, largest_code_block_lines)``."""
    def words(text: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9_]+", text.lower())

    reply_words = words(reply)
    best = 0
    for text in answer_chunk_texts:
        other = set(ngrams(words(text), n))
        for gram in ngrams(reply_words, n):
            if gram in other:
                best = max(best, n)
                break
    blocks = _CODE_FENCE_RE.findall(reply)
    max_lines = max((len(b[1].splitlines()) for b in blocks), default=0)
    return best, max_lines


def ngrams(seq: list[str], n: int) -> set[tuple[str, ...]]:
    if len(seq) < n:
        return set()
    return {tuple(seq[i : i + n]) for i in range(len(seq) - n + 1)}


def socratic_compliance(reply: str) -> bool:
    return bool(_QUESTION_RE.search(reply or ""))


def compliance_verdict(reply: str, max_code_lines: int) -> str:
    """Classify how a bypass attempt was handled.

    Three outcomes, not two. A binary "refused / did not refuse" flag conflates
    an outright refusal ("I can't provide that code") with a soft redirect
    ("Sure thing! Let's focus on setting up...") and scores the second as a
    failure when it is a legitimate Socratic move.

    ``complied``   emitted a copy-pasteable artefact - the real failure mode
    ``redirected`` declined to hand over the answer, without an explicit refusal
    ``refused``    explicitly declined and offered to guide instead
    """
    if max_code_lines > 0:
        return "complied"
    if _REFUSAL_RE.search(reply or ""):
        return "refused"
    return "redirected"


def compliance_honoured(reply: str, max_code_lines: int) -> bool:
    """True unless the tutor handed over the artefact."""
    return compliance_verdict(reply, max_code_lines) != "complied"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
@dataclass
class Turn:
    test_id: str
    question_type: str
    module_id: str
    intent: str
    route: str
    stage: str
    n_chunks: int
    latency_ms: int
    groundedness: Optional[float]
    leak_ngram: int
    max_code_lines: int
    socratic: bool
    refused: bool
    guardrail_flagged: bool
    reply: str
    #: "complied" | "redirected" | "refused" - see :func:`compliance_verdict`.
    compliance: str = "redirected"
    # Claim-level faithfulness against the retrieved material. This is the metric
    # that is actually comparable across routes; ``groundedness`` is not.
    claims: int = 0
    claims_supported: int = 0
    claim_support: Optional[float] = None
    unsupported_examples: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_workflow(no_rag: bool, no_guardrail: bool, no_scaffolding: bool) -> TutoringWorkflow:
    wf = TutoringWorkflow()
    if no_rag:
        wf.retriever = NoRagRetriever()
    if no_guardrail:
        wf.guardrail = PermitAllGuardrail()
    if no_scaffolding:
        wf.intent_agent = AlwaysDirectIntent(wf.intent_agent)
    return wf


def stratified_subset(rows: list[Row], limit: int) -> list[Row]:
    """Pick a balanced, deterministic subset so every config sees the same questions.

    A comparison table is only valid if each configuration is scored on identical
    inputs, and a per-config run must not depend on dict ordering. Questions are
    taken round-robin across question types in ``test_id`` order.
    """
    by_type: dict[str, list[Row]] = {}
    for row in sorted(rows, key=lambda r: r.test_id):
        by_type.setdefault(row.question_type, []).append(row)
    order = sorted(by_type)
    chosen: list[Row] = []
    index = 0
    while len(chosen) < limit and any(by_type[t][index] for t in order if len(by_type[t]) > index):
        for t in order:
            if len(by_type[t]) > index:
                chosen.append(by_type[t][index])
                if len(chosen) == limit:
                    break
        index += 1
    return sorted(chosen, key=lambda r: r.test_id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--test-set", type=Path, default=TEST_SET)
    ap.add_argument("--limit", type=int, default=8,
                    help="Questions to run. Each turn costs a full local generation.")
    ap.add_argument("--module", default=None, help="Restrict to one module_id.")
    ap.add_argument("--no-rag", action="store_true", help="Retrieval disabled.")
    ap.add_argument("--no-guardrail", action="store_true", help="Guardrail approves everything.")
    ap.add_argument("--no-scaffolding", action="store_true", help="Force the direct route.")
    ap.add_argument("--only-types", default=None,
                    help="Comma-separated question types, e.g. conceptual,bypass.")
    ap.add_argument("--stratify", action="store_true",
                    help="Pick a balanced deterministic subset (same questions for every config).")
    ap.add_argument("--out", type=Path, default=None, help="Write per-turn JSON here.")
    ap.add_argument("--label", default="hybrid", help="Name for this configuration.")
    ap.add_argument("--judge", choices=("entail", "llm"), default="entail",
                    help="Faithfulness backend: deterministic entailment, or the local "
                         "model as judge (same model that wrote the answer, so treat "
                         "llm results as a stated limitation).")
    args = ap.parse_args(argv)

    rows = load_rows(args.test_set)
    if args.module:
        rows = [r for r in rows if r.module_id == args.module]
    if args.only_types:
        keep = {t.strip() for t in args.only_types.split(",")}
        rows = [r for r in rows if r.question_type in keep]
    if args.limit:
        rows = stratified_subset(rows, args.limit) if args.stratify else rows[: args.limit]

    config = " + ".join(
        filter(None, [
            args.label,
            "no-rag" if args.no_rag else "",
            "no-guardrail" if args.no_guardrail else "",
            "no-scaffolding" if args.no_scaffolding else "",
        ])
    )
    print("=" * 92)
    print(f"GENERATION EVALUATION   config: {config}")
    print(f"  {len(rows)} questions | every turn is a real local generation")
    print("=" * 92)

    wf = build_workflow(args.no_rag, args.no_guardrail, args.no_scaffolding)
    turns: list[Turn] = []
    failed: list[tuple[str, str]] = []

    for i, row in enumerate(rows, start=1):
        from src.agents.workflow import ChatRequest

        t0 = time.perf_counter()
        try:
            result = wf.handle(
                ChatRequest(
                    message=row.question,
                    module_id=row.module_id,
                    session_id=f"geneval-{args.label}-{row.test_id}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            failed.append((row.test_id, str(exc)[:150]))
            print(f"  [{i}/{len(rows)}] {row.test_id:<9} FAILED: {str(exc)[:110]}")
            continue

        payload = result.to_dict()
        reply = payload["reply"]
        chunks = payload["retrieval"]["chunks"]
        chunk_texts = [c["preview"] for c in chunks]
        answer_texts = [c["preview"] for c in chunks if c.get("is_answer")]

        g = groundedness(reply, chunk_texts)
        leak, code_lines = answer_leak(reply, answer_texts)

        # Claim-level faithfulness needs the full chunk text, not the 240-char
        # preview the API returns, so retrieval is replayed here. It is a few
        # milliseconds and keeps the workflow's response shape unchanged.
        claims = claims_ok = 0
        claim_support = None
        unsupported: list[str] = []
        if chunks:
            from eval.faithfulness import score_claims, summarise

            replay = get_retriever().retrieve(
                row.question, row.module_id, include_patterns=False,
                policy=RetrievalPolicy.default(),
            )
            verdicts = score_claims(
                reply, [c.text for c in replay.chunks], backend=args.judge
            )
            stats = summarise(verdicts)
            claims = stats["claims"]
            claims_ok = stats["supported"]
            claim_support = stats["claim_support"]
            unsupported = [v.claim for v in verdicts if v.verdict == "unsupported"][:3]

        turn = Turn(
            test_id=row.test_id,
            question_type=row.question_type,
            module_id=row.module_id,
            intent=payload["intent"]["label"],
            route=payload["intent"]["route"],
            stage=payload["scaffolding"]["stage"],
            n_chunks=len(chunks),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            groundedness=g,
            leak_ngram=leak,
            max_code_lines=code_lines,
            socratic=socratic_compliance(reply),
            refused=compliance_honoured(reply, code_lines),
            compliance=compliance_verdict(reply, code_lines),
            guardrail_flagged=bool(payload["guardrail"]["flagged"]),
            reply=reply,
            claims=claims,
            claims_supported=claims_ok,
            claim_support=claim_support,
            unsupported_examples=unsupported,
        )
        turns.append(turn)
        gs = f"{g:.3f}" if g is not None else " n/a "
        cs = f"{claim_support:.2f}" if claim_support is not None else " n/a"
        print(f"  [{i}/{len(rows)}] {row.test_id:<9} {row.question_type:<13} "
              f"route={turn.route:<9} stage={turn.stage:<12} chunks={turn.n_chunks} "
              f"ground={gs} support={cs} ({claims_ok}/{claims}) "
              f"leak={turn.leak_ngram} q={'Y' if turn.socratic else 'n'} "
              f"{turn.latency_ms / 1000:.0f}s")

    _report(turns, config, args)
    if failed:
        print(f"\n  {len(failed)} turn(s) FAILED and are absent from every number above.")
        print("  A config is only comparable with another if both scored the same")
        print("  questions, so re-run before building the comparison table:")
        for test_id, err in failed:
            print(f"    - {test_id}: {err}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {"config": config, "environment": _environment(), "turns": [t.__dict__ for t in turns]},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n  per-turn detail written to {args.out}")
    return 0


def _environment() -> dict:
    """Record what produced these numbers.

    A results file that does not say which model wrote the answers, which host ran
    them, or which corpus backed them is not reproducible, and one past outlier
    (a single 990s turn against a 53s median) could not be diagnosed at all because
    the file never recorded the model. The environment is captured for every run
    from now on.
    """
    import platform
    import subprocess

    info: dict = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "embedding_model": settings.embedding_model_name,
        "retrieval_max_distance": settings.retrieval_max_distance,
        "retrieval_expand_on_weak": settings.retrieval_expand_on_weak,
        "retrieval_weak_distance": settings.retrieval_weak_distance,
        "llm_max_tokens": settings.llm_max_tokens,
        "intent_use_llm": settings.intent_use_llm,
        "scaffolding_evidence_based": settings.scaffolding_evidence_based,
        "guardrail_max_code_lines": settings.guardrail_max_code_lines,
        "guardrail_max_words": settings.guardrail_max_words,
    }
    try:
        from src.db import session_scope
        from src.models import LLMConfig
        from sqlalchemy import select

        with session_scope() as session:
            row = session.execute(
                select(LLMConfig).where(LLMConfig.is_active.is_(True)).limit(1)
            ).scalar_one_or_none()
            if row is not None:
                info["llm_provider"] = row.provider
                info["llm_model"] = row.local_model or row.cloud_model
                info["llm_max_tokens"] = row.max_tokens
    except Exception as exc:  # noqa: BLE001 - provenance is best-effort
        info["llm_model"] = f"(unavailable: {exc})"

    try:
        out = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=15
        )
        if out.returncode == 0:
            info["ollama_models"] = [
                line.split()[0] for line in out.stdout.splitlines()[1:] if line.split()
            ]
    except Exception:  # noqa: BLE001
        pass
    return info


def _latency_summary(turns: list[Turn]) -> dict:
    """Latency statistics that survive one pathological turn.

    The mean is dominated by a single stalled turn - 990s against a 53s median
    moved it from 57s to 174s, which is not a description of the system. The
    median and the p95 are reported alongside it, and the worst turns are named so
    an outlier is visible rather than silently averaged in.
    """
    values = sorted(t.latency_ms for t in turns)
    if not values:
        return {}
    n = len(values)

    def percentile(p: float) -> int:
        index = min(n - 1, int(round(p * (n - 1))))
        return values[index]

    worst = sorted(turns, key=lambda t: -t.latency_ms)[:3]
    return {
        "n": n,
        "min_ms": values[0],
        "median_ms": percentile(0.5),
        "p95_ms": percentile(0.95),
        "max_ms": values[-1],
        "mean_ms": sum(values) // n,
        "max_over_median": round(values[-1] / percentile(0.5), 1) if percentile(0.5) else None,
        "worst_turns": [
            {"test_id": t.test_id, "latency_ms": t.latency_ms, "route": t.route}
            for t in worst
        ],
    }


def _report(turns: list[Turn], config: str, args) -> None:
    print("\n" + "=" * 92)
    print(f"RESULTS   config: {config}")
    print("=" * 92)
    if not turns:
        print("  no turns completed")
        return
    n = len(turns)
    gvals = [t.groundedness for t in turns if t.groundedness is not None]
    scaffolded = [t for t in turns if t.route == "scaffold"]
    bypasses = [t for t in turns if t.question_type == "bypass"]

    print(f"  turns completed            : {n}")

    # Headline faithfulness number: share of checkable claims the retrieved
    # material supports. Comparable across routes and configurations, unlike
    # ``groundedness``.
    scored = [t for t in turns if t.claim_support is not None]
    if scored:
        total_claims = sum(t.claims for t in scored)
        total_ok = sum(t.claims_supported for t in scored)
        print(f"  CLAIM SUPPORT              : {total_ok / total_claims:.3f}"
              f"   ({total_ok}/{total_claims} claims, n={len(scored)} turns)")
        worst = sorted(
            (t for t in scored if t.claim_support is not None),
            key=lambda t: t.claim_support,
        )[:3]
        print("    weakest turns:")
        for t in worst:
            print(f"      {t.test_id:<9} {t.claim_support:.2f} ({t.claims_supported}/{t.claims})"
                  f"  {t.route}")
            for ex in t.unsupported_examples[:2]:
                print(f"          unsupported: {ex[:88]}")
    else:
        print("  CLAIM SUPPORT              : n/a - no turn had retrieved material")

    gvals = [t.groundedness for t in turns if t.groundedness is not None]
    if gvals:
        print(f"  mean groundedness (style)  : {sum(gvals) / len(gvals):.3f}   (n={len(gvals)})")
    else:
        print("  mean groundedness (style)  : n/a - retrieval returned nothing for any turn")

    # Groundedness measures how closely the reply tracks the retrieved text, so it
    # is only comparable *within* a route. A Socratic hint deliberately
    # paraphrases and then asks a question, so it scores lower than a direct
    # answer that copies the source. Averaging the two together, or comparing a
    # scaffolded config against a direct one, measures style rather than
    # faithfulness.
    direct = [t for t in turns if t.route == "direct" and t.groundedness is not None]
    scaf = [t for t in turns if t.route == "scaffold" and t.groundedness is not None]
    if direct:
        print(f"    direct-route only        : {sum(t.groundedness for t in direct) / len(direct):.3f}"
              f"   (n={len(direct)})")
    if scaf:
        print(f"    scaffold-route only      : {sum(t.groundedness for t in scaf) / len(scaf):.3f}"
              f"   (n={len(scaf)})")

    print(f"  turns with no context      : {sum(1 for t in turns if t.n_chunks == 0)}/{n}")
    print(f"  any answer leak (8-gram)   : {sum(1 for t in turns if t.leak_ngram >= 8)}/{n}")
    print(f"  largest code block emitted : {max((t.max_code_lines for t in turns), default=0)} lines")
    print(f"  guardrail flagged          : {sum(1 for t in turns if t.guardrail_flagged)}/{n}")
    if scaffolded:
        ok = sum(1 for t in scaffolded if t.socratic)
        print(f"  scaffolded turns asking a question : {ok}/{len(scaffolded)}")
    else:
        print("  scaffolded turns asking a question : n/a (every turn took the direct route)")
    if bypasses:
        complied = sum(1 for t in bypasses if t.compliance == "complied")
        refused = sum(1 for t in bypasses if t.compliance == "refused")
        redirected = sum(1 for t in bypasses if t.compliance == "redirected")
        print(f"  bypass handling                   : {refused} refused, "
              f"{redirected} redirected, {complied} COMPLIED  (n={len(bypasses)})")
        for t in bypasses:
            print(f"      {t.test_id:<9} {t.compliance:<11} "
                  f"code_lines={t.max_code_lines} leak={t.leak_ngram}")
    else:
        print("  bypass handling                   : n/a (no bypass questions completed)")
    latency = _latency_summary(turns)
    print(f"  latency  median / p95 / max       : "
          f"{latency['median_ms'] / 1000:.0f}s / {latency['p95_ms'] / 1000:.0f}s / "
          f"{latency['max_ms'] / 1000:.0f}s")
    print(f"  latency  mean (outlier-sensitive) : {latency['mean_ms'] / 1000:.0f}s "
          f"(max is {latency['max_over_median']}x the median)")
    for entry in latency["worst_turns"]:
        if entry["latency_ms"] > 2 * latency["median_ms"]:
            print(f"      OUTLIER {entry['test_id']:<9} {entry['latency_ms'] / 1000:.0f}s "
                  f"route={entry['route']}  <- report the median, not the mean")

    # A config is only comparable with another if it scored the same questions.
    completed = sorted(t.test_id for t in turns)
    print(f"  questions completed              : {', '.join(completed)}")
    print("=" * 92)


if __name__ == "__main__":
    sys.exit(main())
