"""Hard-delete user sources — operational data, storage, and generated questions."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import ChatMessage, ChatThread, Document, DocumentChunk, Job, LlmResponseCache

logger = logging.getLogger(__name__)


def purge_document(db: Session, doc: Document) -> str:
    """Remove all user-visible and reclaimable data for a document. Returns storage_key."""
    document_id = doc.id
    artifact_id = doc.artifact_id or doc.id
    storage_key = doc.storage_key
    aid = str(document_id)

    assertion_ids = [
        row[0]
        for row in db.execute(
            text("SELECT id FROM intel.assertion WHERE payload->>'artifact_id' = :aid"),
            {"aid": aid},
        ).all()
    ]

    if assertion_ids:
        db.execute(
            text("DELETE FROM intel.assertion_evidence WHERE assertion_id = ANY(:ids)"),
            {"ids": assertion_ids},
        )
        db.execute(
            text("DELETE FROM intel.assertion_participant WHERE assertion_id = ANY(:ids)"),
            {"ids": assertion_ids},
        )
        db.execute(
            text(
                """
                DELETE FROM intel.assertion_lineage
                WHERE from_assertion_id = ANY(:ids) OR to_assertion_id = ANY(:ids)
                """
            ),
            {"ids": assertion_ids},
        )
        db.execute(
            text(
                """
                DELETE FROM intel.assertion_match_candidate
                WHERE assertion_a_id = ANY(:ids) OR assertion_b_id = ANY(:ids)
                """
            ),
            {"ids": assertion_ids},
        )
        db.execute(
            text(
                """
                UPDATE intel.assertion
                SET status = 'retracted',
                    retracted_at = COALESCE(retracted_at, now()),
                    retraction_reason = 'source_deleted',
                    title = NULL,
                    summary = NULL,
                    payload = '{}'::jsonb
                WHERE id = ANY(:ids)
                """
            ),
            {"ids": assertion_ids},
        )

    db.query(LlmResponseCache).filter(LlmResponseCache.artifact_id == artifact_id).delete(
        synchronize_session=False
    )
    db.execute(
        text("DELETE FROM qb.artifact_workspace WHERE artifact_id = :artifact_id"),
        {"artifact_id": artifact_id},
    )

    thread_ids = [t.id for t in db.query(ChatThread.id).filter(ChatThread.artifact_id == artifact_id).all()]
    if thread_ids:
        db.query(ChatMessage).filter(ChatMessage.thread_id.in_(thread_ids)).delete(synchronize_session=False)
        db.query(ChatThread).filter(ChatThread.id.in_(thread_ids)).delete(synchronize_session=False)

    db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete(synchronize_session=False)
    db.query(Job).filter(Job.payload["document_id"].astext == str(document_id)).delete(synchronize_session=False)
    db.delete(doc)

    logger.info("purged document %s (%s assertions retracted)", document_id, len(assertion_ids))
    return storage_key


def purge_ingest_tmp(document_id: uuid.UUID) -> None:
    from app.services.storage import delete_object, ingest_tmp_key

    for stage in ("pages", "chunks"):
        try:
            delete_object(ingest_tmp_key(document_id, stage))
        except Exception:
            logger.debug("ingest tmp missing for %s/%s", document_id, stage, exc_info=True)
