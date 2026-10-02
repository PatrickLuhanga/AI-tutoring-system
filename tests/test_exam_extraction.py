"""Tests for extracting questions out of scanned past papers.

The corpus is scanned images, so everything here is a test of what the extractor
*refuses*. Every function was hardened against a specific real failure in these
papers, and each of those failures looked like a bug in the output rather than in
the code, which is why the cases are pinned as tests rather than checked by eye.
"""

from __future__ import annotations

import pytest

from src.seed_exam_questions import (
    _clean_question_body,
    _has_orphan_numbering,
    _is_lost_stem_block,
    looks_like_question,
    module_for,
    parse_questions,
    pretty_paper_name,
)


# ---------------------------------------------------------------------------
# Filenames -> modules and paper names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "filename,expected",
    [
        ("2023 MIDYEAR MAIN QP INTEGRATIVE PROGRAMMING AND TECHNOLOGIES 3 IPRT301.pdf", "IPRT301"),
        ("2023 MIDYEAR SUPP QP RESEARCH SKILLS RESK401.pdf", "RESK301"),
        ("2024 FEBRUARY SPECIAL QP SOCIAL AND PROFESSIONAL ISSUES SPRI301.pdf", "SPRI301"),
        ("PBDV301 slides.pdf", "PBDV301"),
        ("notes.pdf", None),
    ],
)
def test_module_for_maps_filenames_to_registry_ids(filename, expected):
    # The RESK papers are branded RESK401; the registry calls it RESK301.
    assert module_for(filename) == expected


def test_paper_name_keeps_the_session_so_two_papers_are_distinguishable():
    """Main and supplementary in the same year must not collapse to one label."""
    main = pretty_paper_name("2024 MIDYEAR MAIN QP INTEGRATIVE PROGRAMMING IPRT301")
    supp = pretty_paper_name("2024 MIDYEAR SUPP QP INTEGRATIVE PROGRAMMING IPRT301")
    assert main == "2024 - Midyear Main - IPRT301"
    assert supp == "2024 - Midyear Supp - IPRT301"
    assert main != supp


def test_paper_name_uses_the_registry_id_not_the_branding_on_the_paper():
    name = pretty_paper_name("2023 MIDYEAR SUPP QP RESEARCH SKILLS RESK401")
    assert name.endswith("RESK301")
    assert "RESK401" not in name


def test_paper_name_keeps_february_special_distinct():
    name = pretty_paper_name("2025 FEBRUARY SPECIAL QP INTEGRATIVE PROGRAMMING IPRT301")
    assert name == "2025 - February Special - IPRT301"


# ---------------------------------------------------------------------------
# What counts as a question
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Describe three ways through which biasness occurs during technology development",
        "What is the main challenge with Integrative programming?",
        "Explain why you will choose JSON over XML for data exchange",
    ],
)
def test_real_questions_are_recognised(text):
    assert looks_like_question(text)


@pytest.mark.parametrize(
    "text",
    [
        # Cover page and rubric debris that survived the length filters.
        "Where stated briefly please answer in a few lines and give the page number",
        "HOURS 14HOO to 17HOO 100 marks DR. JULIUS AROBA MRS. DEBORAH OLUWADELE",
        "Answer all the questions on the MCQ card provided and hand it in",
        "This examination consists of 7 pages and a formula sheet for reference",
    ],
)
def test_page_furniture_is_not_a_question(text):
    assert not looks_like_question(text)


# ---------------------------------------------------------------------------
# Column debris
# ---------------------------------------------------------------------------
def test_a_block_whose_numbering_was_separated_from_its_text_is_rejected():
    """OCR reads a numbered column without the questions beside it.

    Stored whole this is ten true/false statements with no way to tell which
    number belongs to which - a student would be handed it as one prompt.
    """
    block = (
        "In each of the following, indicate whether the statement is true or false.\n"
        "1.\n2.\n3.\n4.\n5.\n6.\n7.\n8.\n9.\n10."
    )
    assert _has_orphan_numbering(block)


def test_an_ordinary_numbered_question_is_not_mistaken_for_column_debris():
    assert not _has_orphan_numbering("Write the method calculate() in the class Arithmetic.")


def test_bare_multiple_choice_options_are_not_a_question():
    assert not looks_like_question("A. True B. False C. Neither D. Both")


# ---------------------------------------------------------------------------
# Fill-in-the-blank blocks whose stems were lost
# ---------------------------------------------------------------------------
def test_a_fill_in_the_blank_block_with_no_stems_is_dropped():
    block = (
        "marksl\n2.1.\n2.2.\ncannot do\n2.3.\n"
        "is a habit that inclines people to do what is acceptable"
    )
    assert _is_lost_stem_block(block)


def test_a_question_whose_allocation_carries_its_number_is_kept():
    """"8 marks" is the weight of the question that follows, not debris."""
    block = "8 marks\nThe UML diagram below shows the classes Arithmetic and Tester."
    assert not _is_lost_stem_block(block)


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "block,expected_text,expected_marks",
    [
        ("8 marks\nThe UML diagram below shows the classes Arithmetic and Tester.",
         "The UML diagram below shows the classes Arithmetic and Tester", 8),
        ("marks\nlibrary.dtd contains the DTD below, which defines library.xml.",
         "library.dtd contains the DTD below, which defines library.xml", None),
        ("Describe three ways biasness occurs in tech development [6]",
         "Describe three ways biasness occurs in tech development", 6),
        ("Explain the following terms:\na. Morality\nb. Ethics\ngo MARKS)",
         "Explain the following terms:\na. Morality\nb. Ethics", None),
        ("Describe any three secondary dimensions of diversity\n\nQuestion 5:\nAnswer:\n120 Marks",
         "Describe any three secondary dimensions of diversity", None),
        ("marks\nQuestion 3. Explain the difference between RMI and sockets.",
         "Explain the difference between RMI and sockets", None),
    ],
)
def test_clean_question_body_strips_allocation_and_furniture(block, expected_text, expected_marks):
    text, marks = _clean_question_body(block)
    assert text == expected_text
    assert marks == expected_marks


def test_a_word_starting_with_mar_is_not_a_mark_allocation():
    """"market" and "marker" must survive - a loose "mar?k\\w*" would eat them."""
    body, marks = _clean_question_body("Compare the market share of the two brands in class.")
    assert "market" in body
    assert marks is None


@pytest.mark.parametrize(
    "block,expected",
    [
        # Option letters arrive in their own column, without their text.
        ("Which is the most likely example of a sample frame error?\nA.\nB.\nc.\nD.",
         "Which is the most likely example of a sample frame error?"),
        # A token behind a conjunction the previous block ended on.
        ("and 3. Which best describes this type of data?",
         "Which best describes this type of data?"),
        # Roman numerals used as sub-part markers.
        ("Which skills are needed for a good research proposal?\nii.\niii.\niv.",
         "Which skills are needed for a good research proposal?"),
        # A trailing lone letter with no dot. "Case Study" is option text and
        # stays; only the orphaned "D" goes.
        ("Which research strategy suits developing an AI algorithm?\nCase Study\nD",
         "Which research strategy suits developing an AI algorithm?\nCase Study"),
    ],
)
def test_orphan_enumeration_tokens_are_stripped_but_the_stem_is_kept(block, expected):
    """A line holding only an enumeration token is layout, not content.

    Rejecting the block instead would throw away a real question - most of the
    RESK paper is multiple choice, and its stems survive even though its option
    text does not.
    """
    text, _ = _clean_question_body(block)
    assert text.strip() == expected


def test_option_text_that_is_attached_is_kept():
    """Only bare tokens are stripped. An option with its text is real content."""
    text, _ = _clean_question_body(
        "Which of these is quantitative data?\nA. Data collected from a survey\nB. A pie chart"
    )
    assert "A. Data collected from a survey" in text


def test_a_leading_token_that_is_part_of_a_word_is_not_stripped():
    body, _ = _clean_question_body("3D printing is used in which of these modules?")
    assert body.startswith("3D printing")


def test_sub_part_allocations_do_not_leave_a_trailing_allocation_behind():
    """Regression: the bracketed pass runs before the trailing pass.

    A sub-parted question ends "...(2 marks)\\n(2 marks)\\n(2 marks)". Stripping
    the bracketed allocations exposes a loose one that sits behind them, so a
    trailing pass that ran first simply never saw it.
    """
    block = (
        "Explain the following terms:\na. Morality\nb. Ethics\n"
        "c. Information Ethics\nd. Cyber-ethics\ngo MARKS)\n"
        "(2 marks)\n(2 marks)\n(2 marks)\n(2 marks)"
    )
    text, _ = _clean_question_body(block)
    assert text.endswith("d. Cyber-ethics")
    assert "MARKS" not in text.upper()


def test_cleaning_never_returns_an_empty_prompt():
    """A cleaned-to-nothing block has to be caught by the length check."""
    text, _ = _clean_question_body("marks 8 [6] IPRT301/2023/Main/Paper")
    assert len(text) < 40  # the caller's MIN_CHARS check rejects it


# ---------------------------------------------------------------------------
# The parse loop
# ---------------------------------------------------------------------------
def test_parse_questions_reads_a_realistic_page():
    """Real pages open each block with a numbered or "Question N:" heading."""
    pages = {
        0: (
            "MID YEAR MAIN EXAMINATION QUESTION PAPER\n"
            "Question 4:\n"
            "Describe three ways through which biasness occurs during technology\n"
            "development (6 marks)\n"
            "Question 5:\n"
            "State whether the following is true or false about polymorphism.\n"
        )
    }
    questions = parse_questions("SPRI301", "2024 Midyear Main - SPRI301", pages)
    assert len(questions) == 2
    assert questions[0].prompt.startswith("Describe three ways")
    assert questions[0].marks == 6
    assert questions[0].module_id == "SPRI301"
    assert "(6 marks)" not in questions[0].prompt
    assert questions[1].prompt.startswith("State whether")


def test_parse_questions_drops_the_cover_page():
    pages = {
        0: (
            "BOAMAH-ABU, CHARLES (PhD) IBIDUN OBAGBUWA, CHRISTIANA (PhD)\n"
            "TOTAL 100 MARKS\n"
            "Question 1:\n"
            "Describe three ways through which biasness occurs during technology development.\n"
        )
    }
    questions = parse_questions("SPRI301", "2024 Midyear Main - SPRI301", pages)
    assert len(questions) == 1
    assert "BOAMAH-ABU" not in questions[0].prompt


def test_parse_questions_is_stable_across_pages():
    """The same question continuing onto the next page must not double up."""
    pages = {
        0: "Question 3:\nExplain the difference between RMI and sockets in a distributed application.",
        1: "Question 3:\nExplain the difference between RMI and sockets in a distributed application.",
    }
    questions = parse_questions("IPRT301", "2024 Midyear Main - IPRT301", pages)
    assert len(questions) == 1