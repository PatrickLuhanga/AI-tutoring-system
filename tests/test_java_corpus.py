"""The Java error corpus must stay internally consistent and compilable.

These are the checks that were previously done by hand and by throwaway scripts:
a reviewer had no way to re-run them, so the corpus could regress silently. The
compile check is the one that matters most - a snippet that does not compile is
not teaching material, and a snippet whose stated cause is contradicted by its
own code teaches a falsehood, which already happened once.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.corpora.java_error_corpus import (
    DIFFICULTIES,
    JAVA_ERROR_CORPUS,
    validate_corpus,
)
from tests.conftest import find_javac

#: Imports a lecture-slide fragment would assume. Without them javac reports
#: "cannot find symbol" for List, File and Scanner, which says nothing about
#: whether the teaching point is correct.
IMPORTS = (
    "import java.util.*;\n"
    "import java.io.*;\n"
    "import java.util.stream.*;\n"
    "import java.sql.*;\n"
    "import java.math.BigDecimal;\n"
    "import java.util.Properties;\n"
)

_TYPE_DECL = re.compile(
    r"^\s*(?:public\s+|final\s+|abstract\s+)*(?:class|interface|enum|record)\s+\w+",
    re.MULTILINE,
)

#: Methods supplied by the JDK, so their absence from a snippet is not a defect.
_JDK_METHODS = {
    "println", "print", "length", "size", "get", "add", "set", "remove", "close",
    "readLine", "readAllBytes", "equals", "compareTo", "toUpperCase",
    "toLowerCase", "trim", "substring", "lastIndexOf", "indexOf", "startsWith",
    "endsWith", "append", "count", "filter", "map", "collect", "executeQuery",
    "createStatement", "valueOf", "parseInt", "toString", "hashCode", "abs",
    "getMessage", "divide", "isEmpty", "getAsInt", "next", "nextInt",
    "getKey", "getValue", "containsKey", "getOrDefault", "contains", "stream",
    "split", "replace", "charAt", "intValue", "doubleValue", "hasNext", "toList",
    "sorted", "distinct", "limit", "exists", "getAbsolutePath", "getProperty",
    "of", "toMap", "getConnection", "newInputStream", "empty", "ofNullable",
    "requireNonNullElse", "getOrDefault", "iterate", "chars", "lines", "forEach",
    "getAsDouble", "nextDouble", "nextLine", "hasMoreTokens", "read", "write",
}


# ---------------------------------------------------------------------------
# Structural consistency
# ---------------------------------------------------------------------------
def test_corpus_is_populated():
    assert len(JAVA_ERROR_CORPUS) >= 50, "the design called for roughly 50 patterns"


def test_validate_corpus_passes():
    """The same check ingestion runs, so a bad corpus fails here first."""
    validate_corpus()


def test_error_titles_are_unique():
    """error_title is the upsert conflict target, so a duplicate silently overwrites."""
    titles = [p["error_title"] for p in JAVA_ERROR_CORPUS]
    duplicates = {t for t in titles if titles.count(t) > 1}
    assert not duplicates, f"duplicate error_title would be overwritten: {duplicates}"


def test_difficulties_are_in_range():
    for pattern in JAVA_ERROR_CORPUS:
        assert pattern["difficulty"] in DIFFICULTIES, pattern["error_title"]


def test_tags_are_non_empty_lists():
    for pattern in JAVA_ERROR_CORPUS:
        assert isinstance(pattern["tags"], list) and pattern["tags"], (
            pattern["error_title"]
        )


def test_every_pattern_covers_every_module_it_claims():
    """Most patterns are unscoped; scoped ones must name a real module."""
    from src.config import settings

    known = {record["module_id"] for record in settings.modules.values()}
    for pattern in JAVA_ERROR_CORPUS:
        module_id = pattern.get("module_id")
        if module_id is not None:
            assert module_id in known, (
                f"{pattern['error_title']}: unknown module {module_id!r}"
            )


# ---------------------------------------------------------------------------
# Hint quality: these reach the tutor model verbatim
# ---------------------------------------------------------------------------
def test_hints_ask_a_question_rather_than_state_a_fix():
    """A hint that names the fix collapses the scaffolding it exists to protect.

    The Guardrail Agent only inspects the tutor's reply, so a hint handing over
    the edit would leak past it.
    """
    for pattern in JAVA_ERROR_CORPUS:
        hint = pattern["conceptual_tutor_hint"]
        assert "?" in hint, f"{pattern['error_title']}: hint asks nothing"
        assert hint.rstrip().endswith("?"), (
            f"{pattern['error_title']}: hint should close on the question"
        )


def test_hints_do_not_hand_over_the_answer():
    forbidden = (
        "change this to", "change it to", "replace this with", "use this instead",
        "the fix is", "fix it by", "you should write", "you need to write",
        "correct code", "corrected code", "working code", "paste this",
    )
    for pattern in JAVA_ERROR_CORPUS:
        hint = pattern["conceptual_tutor_hint"].casefold()
        for marker in forbidden:
            assert marker not in hint, (
                f"{pattern['error_title']}: hint states the fix ({marker!r})"
            )


def test_hints_do_not_leak_full_code():
    """A hint containing a whole rewritten program defeats the exercise."""
    for pattern in JAVA_ERROR_CORPUS:
        assert "```" not in pattern["conceptual_tutor_hint"]
        assert "public class" not in pattern["conceptual_tutor_hint"]


def test_hints_only_reference_identifiers_present_in_the_snippet():
    """A hint may point at a name, but not invent one the student cannot see."""
    for pattern in JAVA_ERROR_CORPUS:
        hint = pattern["conceptual_tutor_hint"]
        code = pattern["broken_code"]
        referenced = set(re.findall(r"`(\w+)`", hint)) | set(
            re.findall(r"\b(\w+)\(\)", hint)
        )
        for ident in referenced:
            if ident.isupper() or ident in {"this", "null", "string", "int"}:
                continue
            assert ident in code, (
                f"{pattern['error_title']}: hint references {ident!r}, "
                "which does not appear in the snippet"
            )


def test_common_cause_is_not_a_duplicate_of_the_hint():
    for pattern in JAVA_ERROR_CORPUS:
        assert pattern["common_cause"].strip(), pattern["error_title"]
        assert pattern["common_cause"] != pattern["conceptual_tutor_hint"]


# ---------------------------------------------------------------------------
# Snippet integrity
# ---------------------------------------------------------------------------
def test_snippets_have_balanced_braces():
    for pattern in JAVA_ERROR_CORPUS:
        code = pattern["broken_code"]
        assert code.count("{") == code.count("}"), pattern["error_title"]
        assert code.count("(") == code.count(")"), pattern["error_title"]


def test_snippets_are_not_empty_and_look_like_java():
    java_tokens = (
        "class ", "int ", "String", "new ", "for (", "if (", "Connection",
        "List<", "Map<", "Optional", "Properties", "BigDecimal", "double ",
        "boolean ", "static ", "return ", "try {", "catch", "while (",
    )
    for pattern in JAVA_ERROR_CORPUS:
        code = pattern["broken_code"]
        assert len(code.strip()) > 20, pattern["error_title"]
        assert any(token in code for token in java_tokens), (
            f"{pattern['error_title']}: snippet does not look like Java"
        )


def test_snippets_do_not_call_undefined_helpers():
    """Every method called must be JDK-supplied or declared in the snippet.

    A snippet calling a helper that does not exist leaves the student with code
    they cannot make sense of; several did before this check existed.
    """
    for pattern in JAVA_ERROR_CORPUS:
        code = pattern["broken_code"]
        declared = set(
            re.findall(
                r"\b(?:void|int|double|String|boolean|long|float|char|"
                r"[A-Z]\w*)\s+(\w+)\s*\(",
                code,
            )
        )
        called = set(re.findall(r"\.\s*(\w+)\s*\(", code))
        undeclared = {
            m for m in called if m not in _JDK_METHODS and m not in declared
        }
        assert not undeclared, (
            f"{pattern['error_title']}: calls undefined {sorted(undeclared)}"
        )


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------
def _file_name(source: str) -> str:
    """javac requires the file to be named after its *public* type, if any."""
    m = re.search(
        r"\bpublic\s+(?:final\s+|abstract\s+)*(?:class|interface|enum|record)\s+(\w+)",
        source,
    )
    if m:
        return m.group(1)
    m = re.search(r"(?:class|interface|enum|record)\s+(\w+)", source)
    return m.group(1) if m else "Snippet"


def _close_of_type(code: str, start: int) -> int:
    depth = 0
    seen = False
    for i in range(start, len(code)):
        if code[i] == "{":
            depth += 1
            seen = True
        elif code[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return i + 1
    return -1


#: Depth-0 line starts that are statements rather than declarations, used to split
#: "a few method/field declarations followed by some calls" so the declarations go
#: in the class body and the calls go in a static initialiser.
_STATEMENT_START = re.compile(
    r"^(?:for\s*\(|if\s*\(|while\s*\(|return\b|new\s+|System\.|assert\b|throw\b|try\s*\{"
    r"|[A-Za-z_]\w*\s*(?:\.|\())"
)


def _split_decls_and_statements(code: str) -> tuple[str, str] | None:
    head: list[str] = []
    tail: list[str] = []
    depth = 0
    for line in code.splitlines():
        if depth == 0 and tail:
            tail.append(line)
        elif (
            depth == 0
            and _STATEMENT_START.match(line.strip())
            and not line.strip().endswith("{")
        ):
            tail.append(line)
        else:
            (tail if tail else head).append(line)
        depth += line.count("{") - line.count("}")
    if not tail or not head:
        return None
    return "\n".join(head).strip(), "\n".join(tail).strip()


#: A top-level field declaration: a type, a name, then an optional initialiser.
#: Detected by shape rather than by "has no parentheses", because
#: ``List<String> chosen = new ArrayList<>();`` is a field that contains them.
_FIELD_DECL = re.compile(
    r"^(?:final\s+)?[A-Za-z_][\w<>\[\],\.]*(?:\s*<[^;]*>)?(?:\s*\[\s*\])*\s+"
    r"\w+\s*(?:=[^;]*)?;\s*$"
)

_STATEMENT_KEYWORDS = (
    "for ", "if ", "while ", "return ", "new ", "System.", "throw ", "try ",
    "switch ", "break ", "continue ", "assert ", "this.", "super.", "do ",
)


def _as_static(head: str) -> str:
    """Make top-level field declarations static.

    The synthetic ``main`` is static, so a bare ``int width = 10;`` lifted from a
    fragment would otherwise be an instance field it cannot read. The snippet never
    said static because it was not written to stand alone.

    Depth matters: only lines outside every method body are candidates. Rewriting
    ``size = size * 2;`` inside a method produced ``static size = size * 2;``.
    """
    out: list[str] = []
    depth = 0
    for line in head.splitlines():
        stripped = line.strip()
        is_field = (
            depth == 0
            and bool(stripped)
            and not stripped.startswith(("static ", "class ", "interface ",
                                         "enum ", "record ", "public ", "private ",
                                         "protected ", "final "))
            and not stripped.startswith(_STATEMENT_KEYWORDS)
            and bool(_FIELD_DECL.match(stripped))
        )
        if is_field:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}static {stripped}")
        else:
            out.append(line)
        depth += line.count("{") - line.count("}")
    return "\n".join(out)


def _variants(code: str) -> list[str]:
    """Every context a lecture-slide fragment could legitimately live in.

    These are fragments, not programs: many are bare statements, or a lone method
    with trailing calls. A snippet that compiles in none of these contexts has a
    real problem rather than an awkward one.
    """
    out: list[str] = []
    if _TYPE_DECL.search(code):
        out.append(IMPORTS + code)
        m = _TYPE_DECL.search(code)
        end = _close_of_type(code, m.start())
        if 0 <= end < len(code) and code[end:].strip():
            out.append(
                IMPORTS + code[:end] + f"\n\nstatic {{\n{code[end:].strip()}\n}}\n"
            )
    out.append(IMPORTS + "class Snippet {\n" + code + "\n}\n")
    out.append(
        IMPORTS
        + "class Snippet {\n  public static void main(String[] args) throws Exception {\n"
        + code
        + "\n  }\n}\n"
    )
    split = _split_decls_and_statements(code)
    if split:
        head, tail = split
        out.append(
            IMPORTS
            + "class Snippet {\n"
            + _as_static(head)
            + "\n  public static void main(String[] args) throws Exception {\n"
            + tail
            + "\n  }\n}\n"
        )
        out.append(
            IMPORTS + "class Snippet {\n" + _as_static(head)
            + "\n  static {\n" + tail + "\n  }\n}\n"
        )
    return out


@pytest.mark.parametrize(
    "pattern", JAVA_ERROR_CORPUS, ids=lambda p: p["error_title"][:60]
)
def test_snippet_compiles_or_declares_its_compile_error(pattern):
    """Compile each snippet, or confirm the declared compile failure happens.

    A JDK is not guaranteed to be present, so this skips rather than fails where
    none is installed; it is not a silent pass.
    """
    javac = find_javac()
    if not javac:
        pytest.skip("no javac on this machine; snippets unverified")

    code = pattern["broken_code"]
    expect_error = pattern.get("expects_compile_error", False)
    errors: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        for source in _variants(code):
            path = Path(tmp) / f"{_file_name(source)}.java"
            path.write_text(source, encoding="utf-8")
            proc = subprocess.run(
                [javac, "-nowarn", "-d", tmp, str(path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                assert not expect_error, (
                    f"{pattern['error_title']}: is marked as a compile error "
                    "but built cleanly, so it no longer teaches the failure"
                )
                return
            errors.append(proc.stdout + proc.stderr)

    assert expect_error, (
        f"{pattern['error_title']}: snippet compiled in no context.\n"
        + errors[0][:600]
    )
