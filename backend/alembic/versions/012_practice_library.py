"""Practice library — concept-graph vocab (Wikidata QIDs, participant roles, relations).

No new tables: the practice library is built entirely on the existing intel.*
knowledge graph. This migration only adds the vocabulary rows the concept layer
relies on (entity type, participant role, relation types, activity type) plus the
two platform-owned source slugs. Mirrors backend/scripts/seed_question_vocab.py
so production is self-sufficient without the dev seed.

Revision ID: 012_practice_library
Revises: 011_generation_cache
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text as _sa_text

revision: str = "012_practice_library"
down_revision: Union[str, None] = "011_generation_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (uri, family, label) — mirrors backend/scripts/seed_question_vocab.py
_CONCEPTS = [
    ("/vocab/activity/generate_practice", "activity_type", "Generate practice questions"),
    ("/vocab/entity/concept", "entity_type", "Concept"),
    ("/vocab/role/tests", "participant_role", "Tests concept"),
    ("/vocab/relation/subclass_of", "relation_type", "Subclass of"),
    ("/vocab/relation/instance_of", "relation_type", "Instance of"),
]

# (slug, name) — platform-owned public sources
_SOURCES = [
    ("wikipedia", "Wikipedia"),
    ("practice-library", "Practice library"),
]


def upgrade() -> None:
    bind = op.get_bind()

    for uri, family, label in _CONCEPTS:
        bind.execute(
            _sa_text(
                "INSERT INTO intel.concept (uri, family, label) "
                "VALUES (:uri, :family, :label) ON CONFLICT DO NOTHING"
            ),
            {"uri": uri, "family": family, "label": label},
        )

    # The user_learning domain is always seeded (001 / seed script); reuse it.
    domain_id = bind.execute(
        _sa_text("SELECT id FROM intel.concept WHERE uri = '/vocab/domain/user_learning'")
    ).scalar()
    if domain_id is not None:
        for slug, name in _SOURCES:
            bind.execute(
                _sa_text(
                    "INSERT INTO intel.source (slug, name, domain_concept_id) "
                    "VALUES (:slug, :name, :domain) ON CONFLICT (slug) DO NOTHING"
                ),
                {"slug": slug, "name": name, "domain": domain_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    # Only remove what this migration introduced.
    bind.execute(
        _sa_text("DELETE FROM intel.source WHERE slug IN ('wikipedia', 'practice-library')")
    )
    for uri, _family, _label in reversed(_CONCEPTS):
        bind.execute(
            _sa_text("DELETE FROM intel.concept WHERE uri = :uri"), {"uri": uri}
        )
