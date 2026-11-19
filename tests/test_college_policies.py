from policypilot.college.policies import POLICY_DIR, load_policies, split_policy

DOC = """---
title: Attendance Regulations
effective_date: 2026-06-01
audience: students
---

Preamble that describes the document rather than stating a rule.

## Minimum attendance

At least 75% of classes held,
in each course.

## Disputing an entry

Within 7 days of the class.
"""


def test_one_section_per_heading_with_its_metadata():
    sections = split_policy("attendance", DOC)

    assert [s.metadata["section"] for s in sections] == ["Minimum attendance", "Disputing an entry"]
    first = sections[0]
    assert first.id == "attendance-0"
    assert first.text == "At least 75% of classes held, in each course."
    assert first.metadata["title"] == "Attendance Regulations"
    assert first.metadata["effective_date"] == "2026-06-01"
    assert first.metadata["corpus"] == "college"


def test_the_preamble_is_not_indexed():
    sections = split_policy("attendance", DOC)

    assert all("Preamble" not in s.text for s in sections)


def test_a_document_without_front_matter_still_gets_a_title():
    sections = split_policy("leave_policy", "## Kinds\n\nMedical and personal.\n")

    assert sections[0].metadata["title"] == "Leave Policy"


def test_the_shipped_policies_load_and_cover_the_erp_rules():
    sections = load_policies(POLICY_DIR)
    text = " ".join(s.text for s in sections)

    assert {s.metadata["doc_id"] for s in sections} >= {"attendance", "leave", "examinations"}
    assert "75%" in text
    assert len({s.id for s in sections}) == len(sections)
