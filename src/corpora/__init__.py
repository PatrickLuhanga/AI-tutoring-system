"""Static corpora used to populate the vector store."""

from .java_error_corpus import DEFAULT_MODULE_ID, JAVA_ERROR_CORPUS, validate_corpus

__all__ = ["JAVA_ERROR_CORPUS", "DEFAULT_MODULE_ID", "validate_corpus"]
