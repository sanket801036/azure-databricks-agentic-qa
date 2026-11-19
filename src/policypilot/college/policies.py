"""Loads college policy documents and splits them into citable sections.

A policy is cited by section ("Attendance Regulations > Disputing an attendance entry"),
so the split follows the document's own headings rather than a fixed word window: a
section is the unit a student would quote back, and cutting one in half would leave a
rule without its exceptions.

Each file is Markdown with a small front-matter block:

    ---
    title: Attendance Regulations
    effective_date: 2026-06-01
    audience: students, faculty
    ---
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from policypilot.config import DATA_DIR

POLICY_DIR = DATA_DIR / "college_policies"

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


@dataclass
class PolicySection:
    id: str
    text: str
    metadata: dict


def _front_matter(raw: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, raw[match.end():]


def split_policy(doc_id: str, raw: str) -> list[PolicySection]:
    """One PolicySection per `##` heading. Text before the first heading is the
    document's preamble and is not indexed: it describes the document, it is not a rule."""
    meta, body = _front_matter(raw)
    title = meta.get("title") or doc_id.replace("_", " ").title()
    headings = list(_SECTION_RE.finditer(body))

    sections = []
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        text = " ".join(body[heading.end():end].split())
        if not text:
            continue
        section = heading.group(1)
        sections.append(
            PolicySection(
                id=f"{doc_id}-{i}",
                text=text,
                metadata={
                    "corpus": "college",
                    "doc_id": doc_id,
                    "title": title,
                    "section": section,
                    "effective_date": meta.get("effective_date", ""),
                    "audience": meta.get("audience", ""),
                },
            )
        )
    return sections


def load_policies(policy_dir: Path = POLICY_DIR) -> list[PolicySection]:
    sections: list[PolicySection] = []
    for path in sorted(policy_dir.glob("*.md")):
        sections.extend(split_policy(path.stem, path.read_text(encoding="utf-8")))
    return sections
