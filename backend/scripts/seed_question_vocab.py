#!/usr/bin/env python3
"""Idempotent vocabulary + source seeds for Question Better."""

from __future__ import annotations

import os
import sys

import psycopg

CONCEPTS = [
    ("/vocab/assertion/question.mcq", "assertion_type", "Multiple-choice question"),
    ("/vocab/activity/generate_questions", "activity_type", "Generate questions"),
    ("/vocab/activity/embed_segments", "activity_type", "Embed segments"),
    ("/vocab/activity/evaluate_quality", "activity_type", "Evaluate question quality"),
    ("/vocab/activity/ingest", "activity_type", "Ingest source"),
    ("/vocab/link/variant", "link_type", "Question variant"),
    ("/vocab/link/follow_up_after_miss", "link_type", "Remediation after miss"),
    ("/vocab/link/prerequisite", "link_type", "Prerequisite link"),
    ("/vocab/metric/answer.correct", "metric_type", "Answer correct"),
    ("/vocab/metric/answer.choice_index", "metric_type", "Choice index"),
    ("/vocab/metric/answer.latency_ms", "metric_type", "Answer latency ms"),
    ("/vocab/metric/confidence.self_report", "metric_type", "Self-reported confidence"),
    ("/vocab/metric/hint.used", "metric_type", "Hint used"),
    ("/vocab/projection/student.concept_mastery", "projection_type", "Concept mastery"),
    ("/vocab/projection/student.page_mastery", "projection_type", "Page mastery"),
    ("/vocab/domain/user_learning", "source_domain", "User learning material"),
]

SOURCES = [
    ("user-upload", "User upload"),
    ("url-import", "URL import"),
    ("github-repo", "GitHub repository"),
    ("paste-text", "Pasted text"),
]


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://zivo:zivo@localhost:5455/zivo")
    return url.replace("postgresql+psycopg://", "postgresql://")


def seed() -> None:
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            for uri, family, label in CONCEPTS:
                cur.execute(
                    """
                    INSERT INTO intel.concept (uri, family, label)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (uri, family, label),
                )
            cur.execute(
                "SELECT id FROM intel.concept WHERE uri = '/vocab/domain/user_learning'"
            )
            domain_row = cur.fetchone()
            if not domain_row:
                raise RuntimeError("user_learning domain concept missing")
            domain_id = domain_row[0]
            for slug, name in SOURCES:
                cur.execute(
                    """
                    INSERT INTO intel.source (slug, name, domain_concept_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (slug) DO NOTHING
                    """,
                    (slug, name, domain_id),
                )
        conn.commit()
    print("Vocabulary seeds applied.")


if __name__ == "__main__":
    try:
        seed()
    except Exception as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
