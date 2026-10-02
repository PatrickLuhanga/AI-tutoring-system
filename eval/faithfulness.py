"""Claim-level faithfulness scoring for the generation evaluation.

``groundedness`` in :mod:`eval.run_generation_eval` measures how closely the reply
tracks the retrieved text. That is the wrong instrument for a Socratic tutor: a
hint deliberately paraphrases and then asks a question, so it scores *lower* than
a direct answer that copies the source. Comparing a scaffolded configuration
against a direct one on textual similarity measures style, not faithfulness.

What the paper actually claims is about factual grounding - the 28% factual
error rate the review reports. So this module measures that instead:

``claim_support``
    Every sentence of the reply is classified SUPPORTED / UNSUPPORTED / NONDECLARATIVE
    against the retrieved context. Support means the context entails the claim.
    The headline number is the share of declarative claims that are supported,
    which is directly comparable across configurations and across routes because
    it does not care how the answer is phrased.

Two backends:

``--judge llm``       the local model grades each claim. Fast enough on a small
                      model, but the judge is the same model that wrote the
                      answer, so treat it as a stated limitation, not ground truth.
``--judge entail``    deterministic NLI-style check: a claim is SUPPORTED when a
                      retrieved sentence is close to it, UNSUPPORTED when nothing
                      in the context is. No model call, fully reproducible, but it
                      cannot distinguish a supported claim from a plausible wrong
                      one that happens to share vocabulary.

The two disagree in useful ways, which is why both are available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

Verdict = Literal["supported", "unsupported", "nondclarative"]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)

#: Markdown decoration that carries no propositional content. Stripped before a
#: sentence is judged, so "**PartTime gets calcMonthlyPay()**" is scored on its
#: words rather than on its asterisks.
_MD_DECORATION = re.compile(
    r"(^|\n)\s*(?:>\s*|#{1,6}\s*|[-*+]\s+|\d+[.)]\s*)"   # quote / heading / list
    r"|[*_`~]{1,3}"                                          # emphasis / code
    r"|[\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F]"      # arrows, ticks
    r"|[\U0001F300-\U0001FAFF]"                              # emoji
    ,
    re.MULTILINE,
)

#: Conversational filler and signposts. These are not claims about the subject
#: matter, so scoring them as unsupported would understate real faithfulness.
_FILLER = re.compile(
    r"(?i)^(?:"
    r"good question|great question|nice question|excellent question|"
    r"perfect question|that'?s (?:a )?(?:good|great|great question)|"
    r"you'?re (?:exactly )?right|that'?s right|"
    r"let me (?:give|start|explain|walk|begin)|"
    r"here'?s (?:the|a|how)|"
    r"let'?s (?:start|look|dive|begin|break)|"
    r"i'?ll (?:explain|start|walk|help)|"
    r"before (?:we|you)|"
    r"first,|second,|third,|finally,|"
    r"in summary|to summarise|to summarize|"
    r"hope (?:that|this)|good luck|happy coding|"
    r"does that (?:make sense|help)|"
    r"does this (?:make sense|help)|"
    r"would you like|do you want|shall we|can you (?:try|see)|"
    r"remember that|keep in mind|"
    r"note that|be aware|"
    r"i know|i'?ve got this|you'?ve got this"
    r")\s*(?:[-\u2014\u2013:,.]|$)"
)

#: Parenthetical asides and stage directions ("(No code examples - just your
#: thinking!)"). Useful to a reader, but not assertions about the material.
_ASIDE = re.compile(r"^\s*[([*_].*[)\]*_]\s*$")

#: A sentence that asks something, or invites an answer, is not a claim. Matched
#: anywhere in the sentence, not just at the end: "**Which one do you think
#: applies?** (Just say override)" is still a question.
_QUESTIONISH = re.compile(
    r"(?i)(\?\s*$)|(\b(?:what|why|how|when|where|which|who|whom|whose)\b[^.?!]{0,40}\?)"
    r"|(?:^|\.\s+)(?:what|why|how|when|where|which|who|do|does|did|can|could|"
    r"would|should|is|are|was|were|will|have|has|had|am|tell me|let'?s)\b"
)


def _strip_markdown(text: str) -> str:
    cleaned = _CODE_FENCE.sub(" ", text)
    cleaned = _MD_DECORATION.sub(" ", cleaned)
    return cleaned


def split_sentences(text: str, *, min_words: int = 6) -> list[str]:
    """Split into checkable declarative claims.

    Drops code blocks, questions, conversational filler, signposts, markdown
    decoration and fragments. What survives is a sentence that asserts something
    about the subject matter and can therefore be checked against the retrieved
    material.
    """
    if not text:
        return []
    stripped = _strip_markdown(text)
    out: list[str] = []
    for raw in _SENTENCE_SPLIT.split(stripped):
        s = " ".join(raw.split()).strip(" -—–*·")
        if len(s.split()) < min_words:
            continue
        if "?" in s:
            continue
        if _QUESTIONISH.search(s):
            continue
        if _FILLER.match(s):
            continue
        if _ASIDE.match(s):
            continue
        if s.endswith(":"):
            # Signpost introducing a list or explanation ("Let me give you the
            # plain facts first:"). The claims follow in the next sentence.
            continue
        if not re.search(r"[a-zA-Z]{3}", s):
            continue
        out.append(s)
    return out


@dataclass(slots=True)
class ClaimVerdict:
    claim: str
    verdict: Verdict
    best_match: str = ""
    score: float = 0.0


def _nondclarative(claim: str) -> bool:
    return "?" in claim or bool(_QUESTIONISH.search(claim))


def score_entailment(
    claim: str,
    context_sentences: Sequence[str],
    *,
    threshold: float = 0.55,
) -> tuple[Verdict, str, float]:
    """Deterministic support check: nearest context sentence, cosine above threshold.

    Deliberately conservative about what it will certify. Anything that is not
    clearly close to a retrieved sentence is reported UNSUPPORTED, so the metric
    errs towards flagging rather than excusing a claim.
    """
    if not context_sentences:
        return "unsupported", "", 0.0
    import numpy as np

    from src.embeddings import get_embedder

    emb = get_embedder()
    claim_vec = emb.encode_one(claim)
    ctx_vecs = emb.encode(list(context_sentences))
    sims = np.asarray(ctx_vecs) @ np.asarray(claim_vec)
    best = int(sims.argmax())
    score = float(sims[best])
    verdict: Verdict = "supported" if score >= threshold else "unsupported"
    return verdict, context_sentences[best], score


def judge_with_llm(
    claim: str,
    context: str,
    *,
    router=None,
    max_chars: int = 1200,
) -> tuple[Verdict, str]:
    """Ask the local model whether the context entails the claim.

    The judge is the same local model that produced the answer, so this is not an
    independent assessment. It is reported alongside the deterministic check so a
    disagreement between the two is visible rather than hidden.
    """
    from src.llm_router import LLMError, get_router

    router = router or get_router()
    prompt = (
        "You are grading whether a statement is supported by the given reference text.\n\n"
        f"REFERENCE TEXT:\n{context[:max_chars]}\n\n"
        f"STATEMENT: {claim}\n\n"
        "Reply with exactly one word: SUPPORTED if the reference text states or "
        "directly entails the statement, UNSUPPORTED if it does not. "
        "Ignore whether the statement is pedagogically appropriate."
    )
    try:
        response = router.generate(
            [
                {"role": "system", "content": "You reply with one word."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=8,
        )
    except LLMError:
        return "unsupported", ""
    verdict_text = (response.text or "").strip().lower()
    if "unsupported" in verdict_text:
        return "unsupported", verdict_text
    if "supported" in verdict_text:
        return "supported", verdict_text
    return "unsupported", verdict_text


def score_claims(
    reply: str,
    retrieved_chunks: Sequence[str],
    *,
    backend: str = "entail",
    router=None,
    threshold: float = 0.55,
) -> list[ClaimVerdict]:
    """Score every claim in ``reply`` against the retrieved material."""
    claims = split_sentences(reply)
    if not claims:
        return []
    context_sentences = [s for chunk in retrieved_chunks for s in split_sentences(chunk)]
    context_text = "\n".join(retrieved_chunks)

    results: list[ClaimVerdict] = []
    for claim in claims:
        if backend == "llm":
            verdict, note = judge_with_llm(claim, context_text, router=router)
            results.append(ClaimVerdict(claim=claim, verdict=verdict, best_match=note))
        else:
            verdict, match, score = score_entailment(
                claim, context_sentences, threshold=threshold
            )
            results.append(
                ClaimVerdict(claim=claim, verdict=verdict, best_match=match, score=score)
            )
    return results


def summarise(verdicts: Sequence[ClaimVerdict]) -> dict:
    """Aggregate to the headline number: share of checkable claims that are supported."""
    total = len(verdicts)
    supported = sum(1 for v in verdicts if v.verdict == "supported")
    return {
        "claims": total,
        "supported": supported,
        "unsupported": total - supported,
        "claim_support": (supported / total) if total else None,
    }
