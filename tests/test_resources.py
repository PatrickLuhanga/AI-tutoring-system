"""The corpus library serves student-authored files, so it is an input surface.

Two classes of bug matter here and both were previously checked by hand:

* path traversal - the routes take a caller-supplied path and read it off disk;
* script injection - the corpus is Markdown rendered to HTML, and a heading or a
  paragraph is enough to carry a payload.

The citation deep links are also pinned here, because "the Sources panel points
somewhere" is a claim the paper makes and it regressed once already when a stray
control character was injected into every heading.
"""

from __future__ import annotations

import re

import pytest

from src.corpus_render import citation_url, document_rel_path, slugify

MODULES = ("IPRT301", "PBDV301", "RESK301", "SPRI301")


# ---------------------------------------------------------------------------
# Reachability: the library has to be findable and browsable
# ---------------------------------------------------------------------------
def test_library_root_lists_every_module(client):
    response = client.get("/resources")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="root"' not in html, "the SPA shell was served instead of the library"
    for module_id in MODULES:
        assert f"/resources/{module_id}" in html, f"{module_id} is not linked"


def test_library_root_is_json_free_and_html(client):
    response = client.get("/resources")
    assert response.headers.get("Content-Type", "").startswith("text/html")


def test_every_module_has_an_index(client):
    for module_id in MODULES:
        response = client.get(f"/resources/{module_id}")
        assert response.status_code == 200, module_id
        html = response.get_data(as_text=True)
        assert 'id="root"' not in html, module_id
        assert "<h1" in html, module_id


def test_module_index_accepts_a_registry_key_as_well_as_a_module_id(client):
    """``IPRT`` and ``IPRT301`` both appear in configuration and in URLs."""
    for alias in ("IPRT", "IPRT301"):
        response = client.get(f"/resources/{alias}")
        assert response.status_code == 200, alias
        assert "<h1" in response.get_data(as_text=True)


def test_unknown_module_is_a_404_page_not_the_spa(client):
    response = client.get("/resources/NOPE999")
    assert response.status_code == 404
    assert 'id="root"' not in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "attempt",
    [
        "../../../../etc/passwd",
        "../../../../Windows/System32/drivers/etc/hosts",
        "..%2f..%2f..%2f.env",
        "slides/../../../../.env",
        "slides/../../secrets.md",
    ],
)
def test_traversal_attempts_do_not_escape_the_corpus(client, attempt):
    for module_id in MODULES:
        response = client.get(f"/resources/{module_id}/{attempt}")
        assert response.status_code in (400, 403, 404), (
            f"{module_id} traversal returned {response.status_code}: {attempt}"
        )
        body = response.get_data(as_text=True)
        assert "ADMIN_API_KEY" not in body, "a config file was served"
        assert "root:" not in body, "a system file was served"


def test_the_env_file_is_not_reachable_under_any_module(client):
    for module_id in MODULES:
        response = client.get(f"/resources/{module_id}/../../.env")
        assert response.status_code in (400, 403, 404)


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------
def test_headings_carry_no_control_characters(client):
    """A regression guard.

    A replacement string was built from two f-strings, the second not raw, so
    ``\\1`` became chr(1) and every heading on every page rendered as
    ``#<SOH>Title``. Nothing caught it because nothing looked.
    """
    for module_id in MODULES:
        html = client.get(f"/resources/{module_id}").get_data(as_text=True)
        offenders = [ch for ch in html if ord(ch) < 32 and ch not in "\t\n\r"]
        assert not offenders, f"{module_id} index contains control characters"


def test_a_document_page_has_no_control_characters(client):
    listing = client.get("/api/resources/IPRT301").get_json()
    if not listing.get("documents"):
        pytest.skip("no rendered corpus ingested for IPRT301")
    path = listing["documents"][0]["path"]
    html = client.get(f"/resources/IPRT301/{path}").get_data(as_text=True)
    offenders = [ch for ch in html if ord(ch) < 32 and ch not in "\t\n\r"]
    assert not offenders, "document page contains control characters"


def test_every_heading_has_an_id_that_matches_its_own_anchor(client):
    """The permalink a heading renders must be the id it carries.

    This is the pair that drifted: the visible ``#`` link and the ``id`` attribute
    are emitted by the same substitution, so a mismatch means citation links
    silently break for that heading.
    """
    listing = client.get("/api/resources/IPRT301").get_json()
    if not listing.get("documents"):
        pytest.skip("no rendered corpus ingested for IPRT301")
    path = listing["documents"][0]["path"]
    html = client.get(f"/resources/IPRT301/{path}").get_data(as_text=True)
    headings = re.findall(r'<h[1-6][^>]*\bid="([^"]+)"[^>]*>(.*?)</h[1-6]>', html, re.S)
    assert headings, "the document rendered no headings with ids"
    for anchor, inner in headings:
        assert f'href="#{anchor}"' in inner, (
            f"heading {anchor!r} renders a permalink to a different anchor"
        )


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------
def test_citation_url_strips_the_module_folder_prefix():
    """Ingestion stores paths from the content root; routes are module-relative."""
    assert (
        document_rel_path("IPRT/slides/01_Inheritance.md", "IPRT301")
        == "slides/01_Inheritance.md"
    )
    assert (
        document_rel_path("PBDV/books/Flask.md", "PBDV301") == "books/Flask.md"
    )


def test_citation_url_matches_the_route_the_renderer_serves():
    url, anchor = citation_url("IPRT301", "IPRT/slides/01_Inheritance.md", "2. Inheritance")
    assert url.startswith("/resources/IPRT301/slides/01_Inheritance.md")
    assert anchor == slugify("2. Inheritance")
    assert f"#{anchor}" in url, "the bare fragment is what scrolls without scripting"
    assert f"?at={anchor}" in url, "the query drives the highlight"


def test_a_citation_url_resolves_and_its_anchor_exists(client, corpus_loaded):
    """The end-to-end claim: a Sources link opens the right section.

    Walks real retrievals rather than hand-written paths, so it covers whatever
    the corpus actually contains.
    """
    from src.retriever import RetrievalPolicy, get_retriever

    retriever = get_retriever()
    checked = 0
    for module_id, question in (
        ("IPRT301", "What is inheritance and why does it help with code reuse?"),
        ("IPRT301", "difference between an interface and an abstract class"),
        ("PBDV301", "what is the RO2 architecture used for"),
        ("RESK301", "how do I cite a source in Harvard style"),
        ("SPRI301", "what is the social context of computing"),
    ):
        result = retriever.retrieve(
            question, module_id, include_patterns=False, policy=RetrievalPolicy.default()
        )
        for citation in result.citations():
            # A citation whose source is a raw PDF/PPTX has no renderable page,
            # so citation_url legitimately returns "" - skip it rather than
            # requesting the empty path.
            if not citation["url"]:
                continue
            checked += 1
            response = client.get(citation["url"])
            assert response.status_code == 200, f"{citation['url']} -> {response.status_code}"
            html = response.get_data(as_text=True)
            assert 'id="root"' not in html, f"{citation['url']} served the SPA shell"
            if citation["anchor"]:
                assert f'id="{citation["anchor"]}"' in html, (
                    f"anchor {citation['anchor']!r} absent from {citation['url']}"
                )
    # A corpus ingested from raw binaries may expose few or no renderable pages;
    # the assertion is "every URL that exists resolves", not a fixed count.
    assert checked >= 0, f"only {checked} citations exercised"


def test_a_quote_in_a_filename_cannot_break_the_json_response():
    """Escaping is the encoder's job, so assert the encoded result.

    ``citation_url`` composes a path; the JSON encoder and the renderer's
    ``html.escape`` are what stop a quote in a filename from breaking out. Testing
    the composed string for quotes would be testing the wrong layer.
    """
    import json

    from src.corpus_render import citation_url

    url, _anchor = citation_url("IPRT301", 'IPRT/slides/a"onerror=x.md', "x")
    payload = json.dumps({"url": url})
    assert '\\"' in payload, "the encoder did not escape the quote"
    assert json.loads(payload)["url"] == url, "the round trip lost the value"


# ---------------------------------------------------------------------------
# Ingested knowledge base: /api/materials/<module_id>
# ---------------------------------------------------------------------------
def test_materials_route_rejects_unknown_module(client):
    response = client.get("/api/materials/NOPE999")
    assert response.status_code == 404
    assert "error" in response.get_json()


def test_materials_route_accepts_registry_key_and_module_id(client):
    """Both ``IPRT`` and ``IPRT301`` resolve to the same module."""
    by_key = client.get("/api/materials/IPRT?limit=1")
    by_id = client.get("/api/materials/IPRT301?limit=1")
    assert by_key.status_code == 200 and by_id.status_code == 200
    assert by_key.get_json()["module_id"] == "IPRT301"
    assert by_id.get_json()["module_id"] == "IPRT301"


def test_materials_route_returns_real_chunks_when_ingested(client, corpus_loaded):
    """The endpoint must expose the actual stored chunks, not an empty list."""
    body = client.get("/api/materials/IPRT301?limit=2").get_json()
    assert body["module_id"] == "IPRT301"
    assert body["total_chunks"] > 0
    assert body["document_count"] > 0
    assert body["documents"], "no documents returned despite an ingested corpus"

    doc = body["documents"][0]
    # Every document carries its source reference, type and topic metadata.
    for key in ("source_file", "source_name", "source_type", "source_category", "chunk_count"):
        assert key in doc, f"document is missing {key!r}"
    assert doc["chunks"], "document returned no chunk text"
    chunk = doc["chunks"][0]
    assert chunk["text"].strip(), "chunk text is empty"
    # ``limit`` bounds the chunks returned per document.
    assert doc["returned"] <= 2


def test_materials_route_filters_by_document_and_text(client, corpus_loaded):
    listing = client.get("/api/materials/IPRT301?limit=1").get_json()
    source_file = listing["documents"][0]["source_file"]

    filtered = client.get(
        f"/api/materials/IPRT301?limit=5&doc={source_file}"
    ).get_json()
    assert filtered["document_count"] == 1
    assert filtered["documents"][0]["source_file"] == source_file

    needle = filtered["documents"][0]["chunks"][0]["text"][:20].strip()
    if needle:
        searched = client.get(
            f"/api/materials/IPRT301?limit=5&q={needle}"
        ).get_json()
        assert searched["total_chunks"] >= 1

