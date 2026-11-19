"""Index the college policies: `python -m policypilot.college.ingest`.

Local only for now - the policies go into their own Chroma collection beside the 10-K one,
so the two corpora never mix in a search. On Databricks they would be their own UC table
and Delta-Sync index the same way the filings are; see README "College mode".
"""

from __future__ import annotations

import logging

from policypilot.college.policies import load_policies
from policypilot.retrieval.local_chroma import LocalChromaVectorStore

COLLEGE_COLLECTION = "policypilot_college"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_college_store() -> LocalChromaVectorStore:
    return LocalChromaVectorStore(collection=COLLEGE_COLLECTION)


def run_college_ingestion() -> int:
    sections = load_policies()
    store = get_college_store()
    store.upsert(
        ids=[s.id for s in sections],
        texts=[s.text for s in sections],
        metadatas=[s.metadata for s in sections],
    )
    docs = {s.metadata["doc_id"] for s in sections}
    logger.info("Indexed %d policy sections from %d documents", len(sections), len(docs))
    return len(sections)


if __name__ == "__main__":
    run_college_ingestion()
