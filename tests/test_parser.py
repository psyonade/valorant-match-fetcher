import copy
import json
import re
from pathlib import Path

from parser import flatten_match


FIXTURE = Path(__file__).parent / "fixtures" / "sample_match.json"


def sample_match():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_flatten_maps_live_performance_score_and_team_context():
    row, kills = flatten_match(sample_match(), "TestPlayer", "TEST", include_rounds=True)

    assert row["season"] == "e11a6"
    assert row["perf_score"] == 262.88411032045906
    assert row["perf_rank_in_match"] is not None
    assert row["perf_rank_in_team"] is not None
    assert row["kd"] is not None
    assert row["team_avg_perf_score"] is not None
    assert row["enemy_avg_perf_score"] is not None
    assert row["team_avg_adr"] is not None
    assert row["ps_component_adjusted_kills"] is not None
    assert len(kills) == 198
    assert kills[0]["headshot"] is None
    assert set(kills[0]) == {
        "match_id", "round", "time_in_round_ms", "killer_is_me", "victim_is_me", "killer_agent",
        "victim_agent", "weapon", "headshot", "assist_is_me", "side",
    }


def test_flatten_keeps_missing_performance_and_optional_fields_null():
    raw = copy.deepcopy(sample_match())
    player = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    player.pop("performance")
    player.pop("economy")
    player.pop("tier")
    player["stats"].pop("damage")
    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["perf_score"] is None
    assert row["perf_rank_in_match"] is None
    assert row["perf_rank_in_team"] is None
    assert row["adr"] is None
    assert row["spent_total"] is None
    assert row["rank_tier"] is None
    assert row["damage_dealt"] is None
    assert row["clutches"] is None
    assert row["rr_change"] is None


def test_fixture_uses_synthetic_player_ids():
    raw_text = FIXTURE.read_text(encoding="utf-8")
    player_ids = re.findall(r'"puuid":"([^"]+)"', raw_text)
    assert player_ids
    assert all(re.fullmatch(r"player\d+", player_id) for player_id in player_ids)

