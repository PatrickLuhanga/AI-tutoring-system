"""Hybrid AI Tutoring System.

Tier 1 (Hybrid Data Tier): PostgreSQL relational store, pgvector vector store
and the ingestion pipelines that populate them.

Tier 2 (Orchestration Tier): the Flask API gateway, the multi-agent Socratic
RAG workflow and the Dynamic LLM Router that targets local Ollama or a cloud
API depending on the admin-selected configuration.
"""

__version__ = "0.2.0"
