"""Orchestration agents (Tier 2).

Each agent has exactly one responsibility (architecture section 21) and lives in
its own module so the pipeline is easy to read, test and replace:

* :mod:`~src.agents.intent_agent`  - Intent Agent (section 6)
* :mod:`~src.agents.scaffolding`   - Scaffolding Engine (section 7)
* :mod:`~src.agents.tutor_agent`   - Tutor Agent (sections 7-9)
* :mod:`~src.agents.guardrail`     - Guardrail Agent (section 11)
* :mod:`~src.agents.workflow`      - the workflow that coordinates them

The RAG retrieval step lives in :mod:`src.retriever` and the resilient Ollama
client in :mod:`src.inference`.
"""

from .guardrail import GuardrailAgent, GuardrailResult
from .intent_agent import Intent, IntentAgent
from .scaffolding import ScaffoldingLayer
from .tutor_agent import TutorAgent
from .workflow import ChatRequest, TutoringWorkflow, WorkflowResult

__all__ = [
    "ChatRequest",
    "GuardrailAgent",
    "GuardrailResult",
    "Intent",
    "IntentAgent",
    "ScaffoldingLayer",
    "TutorAgent",
    "TutoringWorkflow",
    "WorkflowResult",
]
