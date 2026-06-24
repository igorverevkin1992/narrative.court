"""Topic Bank persistence (Module 10) -- CRUD over the topics table."""
from __future__ import annotations

from modules.db import TopicRow, get_session
from modules.schemas import Topic


def save_topic(topic: Topic) -> None:
    with get_session() as s:
        row = s.get(TopicRow, str(topic.id))
        if row is None:
            row = TopicRow(id=str(topic.id))
            s.add(row)
        row.thesis = topic.thesis
        row.category = topic.category
        row.monetization_risk = topic.monetization_risk
        row.deepseek_risk = topic.deepseek_risk
        row.status = topic.status.value
        row.auto_status = topic.checklist.auto_status
        row.payload_json = topic.model_dump_json()
        s.commit()


def list_topics(status: str | None = None) -> list[Topic]:
    with get_session() as s:
        q = s.query(TopicRow) if hasattr(s, "query") else None
        rows = q.all() if q is not None else []
        topics = [Topic.model_validate_json(r.payload_json) for r in rows]
    if status:
        topics = [t for t in topics if t.status.value == status]
    return topics
