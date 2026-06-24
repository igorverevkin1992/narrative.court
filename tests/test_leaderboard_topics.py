"""Phase-3 tests: leaderboard recompute/metrics/export, Oxford-delta outcomes,
the leaderboard service round-trip, and Topic Bank CRUD + checklist."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from modules.config import load_config
from modules.episodes.manager import save_episode
from modules.leaderboard.engine import (
    model_meta_from_config,
    outcome_from_episode,
    recompute,
)
from modules.leaderboard.export import to_csv, to_markdown
from modules.leaderboard.service import collect_outcomes, record_delta
from modules.schemas import (
    BehaviourFlag,
    Episode,
    LeaderboardEntry,
    ObjectionEvent,
    OxfordDelta,
    Side,
)

_MODELS = [
    {"id": "gpt-5.5", "display_name": "GPT-5.5", "season": 1},
    {"id": "deepseek-v4-pro", "display_name": "DeepSeek V4 Pro", "season": 1},
]


def _episode_with_events(slug: str = "ep_lb_test") -> Episode:
    ep = Episode(thesis="The dissolution of the USSR was inevitable.", slug=slug,
                 prosecution_model_id="gpt-5.5", defense_model_id="deepseek-v4-pro")
    ep.objections = [
        ObjectionEvent(round_id="r1_prosecution", side=Side.PROSECUTION, claim="claim A", ruling="sustained"),
        ObjectionEvent(round_id="r1_defense", side=Side.DEFENSE, claim="claim B", ruling="overruled"),
    ]
    ep.behaviour_flags = [BehaviourFlag(
        flag_type="REFUSED", confidence=1.0, evidence="Sorry, beyond scope",
        rule_triggered="rule2_refusal_phrase", model_id="deepseek-v4-pro", round_id="r1_defense")]
    return ep


def _prosecution_wins_delta(ep: Episode) -> OxfordDelta:
    # Δpros = 60-40 = 20 ; Δdef = 35-30 = 5 -> prosecution wins, quorum present.
    return OxfordDelta(episode_id=ep.id, agree_before=40, agree_after=60,
                       disagree_before=30, disagree_after=35, votes_before=100, votes_after=100)


def test_outcome_and_recompute_metrics():
    ep = _episode_with_events()
    out = outcome_from_episode(ep, _prosecution_wins_delta(ep))
    assert out.winner_model_id == "gpt-5.5"

    entries = {e.model_id: e for e in recompute([out], model_meta_from_config(_MODELS))}
    g, d = entries["gpt-5.5"], entries["deepseek-v4-pro"]
    assert g.win_count == 1 and g.win_streak == 1
    assert d.win_count == 0 and d.win_streak == 0
    assert g.objection_sustained_pct == 100.0   # 1 sustained / 1 total
    assert d.objection_sustained_pct == 0.0      # 0 sustained / 1 total
    assert d.explicit_refusal_pct == 100.0       # deepseek, 1 refused episode / 1
    assert g.explicit_refusal_pct is None        # non-deepseek -> n/a
    assert g.avg_sustained_per_episode == 1.0


def test_no_quorum_yields_no_winner():
    ep = _episode_with_events()
    delta = OxfordDelta(episode_id=ep.id, agree_before=40, agree_after=60,
                        disagree_before=30, disagree_after=35, votes_before=10, votes_after=100)
    assert delta.no_quorum is True
    out = outcome_from_episode(ep, delta)
    assert out.winner_model_id is None
    entries = recompute([out], model_meta_from_config(_MODELS))
    assert all(e.win_count == 0 for e in entries)


def test_export_markdown_and_csv():
    ep = _episode_with_events()
    entries = recompute([outcome_from_episode(ep, _prosecution_wins_delta(ep))],
                        model_meta_from_config(_MODELS))
    md = to_markdown(entries, season=1, episode_no=1, total=10, last_episode_title="USSR")
    assert "Standings" in md and "GPT-5.5" in md and "Ep.1" in md

    rows = list(csv.reader(io.StringIO(to_csv(entries))))
    assert rows[0][0] == "model_id"
    assert any(r[0] == "gpt-5.5" for r in rows[1:])


def test_record_delta_sets_leaderboard_result():
    ep = _episode_with_events()
    lr = record_delta(ep, _prosecution_wins_delta(ep))
    assert lr["winner_model_id"] == "gpt-5.5"
    assert lr["no_quorum"] is False
    assert ep.leaderboard_result["delta"]["votes_after"] == 100


def test_service_collect_outcomes_from_saved_episode(tmp_path):
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")
    ep = _episode_with_events("ep_lb_saved")
    record_delta(ep, _prosecution_wins_delta(ep))
    save_episode(ep, config)  # writes data/episodes/<slug>/episode.json

    outcomes = collect_outcomes(config)
    assert len(outcomes) == 1
    assert outcomes[0].winner_model_id == "gpt-5.5"


def test_collect_outcomes_orders_chronologically(tmp_path):
    """P0-2: outcomes ordered by created_at (oldest first) so win-streak is
    chronological regardless of save order / file mtime."""
    config = load_config()
    config._data["app"]["episodes_dir"] = str(tmp_path / "episodes")

    old = _episode_with_events("ep_old")
    old.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new = _episode_with_events("ep_new")
    new.created_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    record_delta(old, _prosecution_wins_delta(old))
    record_delta(new, _prosecution_wins_delta(new))

    # Save oldest-created first so mtime order (newest-first) differs from
    # chronological order; the fix must still yield [old, new].
    save_episode(old, config)
    save_episode(new, config)

    outcomes = collect_outcomes(config)
    assert [o.episode_id for o in outcomes] == [str(old.id), str(new.id)]


def test_topic_crud_and_checklist(tmp_path):
    from modules.db import delete_topic, init_db
    from modules.schemas import Topic, TopicChecklist
    from modules.topics.bank import list_topics, save_topic

    init_db(tmp_path / "court.db")
    checklist = TopicChecklist(
        has_evidence_both_sides=True, is_debatable=True, deepseek_risk="low",
        monetization_risk="green", reach_vs_safety=4, freshness_vs_evergreen=3)
    assert checklist.auto_status == "approved"

    topic = Topic(thesis="The dissolution of the USSR was inevitable.", category="optimal",
                  monetization_risk="green", deepseek_risk="low",
                  recommended_pair=("gpt-5.5", "deepseek-v4-pro"), checklist=checklist)
    save_topic(topic)
    assert any(t.thesis == topic.thesis for t in list_topics())

    delete_topic(str(topic.id))
    assert all(str(t.id) != str(topic.id) for t in list_topics())


def test_rejected_topic_when_deepseek_guaranteed_refused():
    from modules.schemas import TopicChecklist
    checklist = TopicChecklist(
        has_evidence_both_sides=True, is_debatable=True, deepseek_risk="guaranteed_refused",
        monetization_risk="green", reach_vs_safety=3, freshness_vs_evergreen=3)
    assert checklist.auto_status == "rejected"


def test_save_leaderboard_persists(tmp_path):
    from modules.db import LeaderboardRow, get_session, init_db, save_leaderboard

    init_db(tmp_path / "court.db")
    save_leaderboard([LeaderboardEntry(
        model_id="gpt-5.5", display_name="GPT-5.5", season=1, win_count=2,
        objection_sustained_pct=75.0, avg_sustained_per_episode=1.5, win_streak=2)])
    with get_session() as s:
        rows = s.query(LeaderboardRow).all()
    assert any(r.model_id == "gpt-5.5" and r.win_count == 2 for r in rows)
