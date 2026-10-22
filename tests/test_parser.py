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
    assert row["result"] == "win"
    assert row["won"] is True
    assert row["round_records_total"] == 26
    assert row["rounds_with_valid_result"] == 26
    assert row["round_validation_warning"] is None
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


def test_draw_is_not_misclassified_from_provider_false():
    raw = sample_match()
    own_team = next(team for team in raw["teams"] if team["team_id"] == "Blue")
    enemy_team = next(team for team in raw["teams"] if team["team_id"] == "Red")
    own_team["won"] = False
    own_team["rounds"] = {"won": 15, "lost": 15}
    enemy_team["won"] = False
    enemy_team["rounds"] = {"won": 15, "lost": 15}
    for number in range(26, 30):
        rnd = copy.deepcopy(raw["rounds"][number % len(raw["rounds"])])
        rnd["id"] = number
        rnd["winning_team"] = "Blue" if number % 2 == 0 else "Red"
        raw["rounds"].append(rnd)

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["won"] is False  # Source/API field remains unchanged.
    assert row["result"] == "draw"
    assert row["round_records_total"] == 30
    assert row["rounds_with_valid_result"] == 30
    assert row["round_validation_warning"] is None


def test_round_placeholders_are_counted_but_not_treated_as_results():
    raw = sample_match()
    raw["rounds"].extend([
        {"id": 26, "winning_team": None},
        {"id": 27, "winning_team": None},
        {"id": 28},
    ])

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["round_records_total"] == 29
    assert row["rounds_with_valid_result"] == 26
    assert row["round_validation_warning"] is None


def test_round_counts_distinguish_missing_empty_and_mismatched_data():
    raw = sample_match()
    raw.pop("rounds")
    missing, _ = flatten_match(raw, "TestPlayer", "TEST")
    assert missing["round_records_total"] is None
    assert missing["rounds_with_valid_result"] is None
    assert missing["round_validation_warning"] is None

    raw = sample_match()
    raw["rounds"] = []
    empty, _ = flatten_match(raw, "TestPlayer", "TEST")
    assert empty["round_records_total"] == 0
    assert empty["rounds_with_valid_result"] == 0
    assert "(0)" in empty["round_validation_warning"]
    assert "(26)" in empty["round_validation_warning"]

    raw = sample_match()
    raw["rounds"].pop()
    mismatched, _ = flatten_match(raw, "TestPlayer", "TEST")
    assert mismatched["rounds_with_valid_result"] == 25
    assert mismatched["round_validation_warning"] is not None


def test_provider_false_without_round_totals_is_unknown():
    raw = sample_match()
    for team in raw["teams"]:
        team["won"] = False
        team.pop("rounds")

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["won"] is False
    assert row["result"] == "unknown"


def test_result_can_fall_back_to_unambiguous_provider_team_boolean():
    raw = sample_match()
    for team in raw["teams"]:
        team.pop("rounds")
    own_team = next(team for team in raw["teams"] if team["team_id"] == "Blue")
    enemy_team = next(team for team in raw["teams"] if team["team_id"] == "Red")
    own_team["won"] = False
    enemy_team["won"] = True

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["result"] == "loss"


def _event(round_number, killer, victim, moment):
    event = {"round": round_number, "killer": killer, "victim": victim}
    if moment is not None:
        event["time_in_round_in_ms"] = moment
    return event


def test_self_and_environmental_events_do_not_count_as_first_deaths_or_multikills():
    raw = sample_match()
    me = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    raw["kills"] = [
        _event(1, {"puuid": me["puuid"]}, {"puuid": me["puuid"]}, 100),
        _event(1, {"puuid": me["puuid"]}, {"puuid": next(p for p in raw["players"] if p["team_id"] == me["team_id"] and p["puuid"] != me["puuid"])["puuid"]}, 150),
        _event(1, None, {"puuid": me["puuid"]}, 200),
        _event(1, {"puuid": "missing-player"}, {"puuid": me["puuid"]}, 300),
    ]

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["first_deaths"] == 0
    assert row["multikills_2k"] == 0
    assert row["multikills_3k"] == 0


def test_multikills_count_distinct_victims_and_ignore_duplicate_event_rows():
    raw = sample_match()
    me = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    opponents = [p for p in raw["players"] if p["team_id"] != me["team_id"]]
    raw["kills"] = [
        _event(1, {"puuid": me["puuid"]}, {"puuid": opponents[0]["puuid"]}, 100),
        _event(1, {"puuid": me["puuid"]}, {"puuid": opponents[0]["puuid"]}, 100),
        _event(1, {"puuid": me["puuid"]}, {"puuid": opponents[1]["puuid"]}, 200),
        _event(1, {"puuid": me["puuid"]}, {"puuid": opponents[2]["puuid"]}, 300),
    ]

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["multikills_2k"] == 0
    assert row["multikills_3k"] == 1


def test_first_death_uses_earliest_timed_opponent_kill():
    raw = sample_match()
    me = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    opponents = [p for p in raw["players"] if p["team_id"] != me["team_id"]]
    raw["kills"] = [
        _event(1, {"puuid": opponents[0]["puuid"]}, {"puuid": me["puuid"]}, 50),
        _event(1, {"puuid": "unknown"}, {"puuid": me["puuid"]}, 60),
    ]

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["first_deaths"] == 1


def test_first_death_is_null_when_earliest_timestamp_is_ambiguous():
    raw = sample_match()
    me = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    opponents = [p for p in raw["players"] if p["team_id"] != me["team_id"]]
    raw["kills"] = [
        _event(2, {"puuid": opponents[0]["puuid"]}, {"puuid": me["puuid"]}, 50),
        _event(2, {"puuid": me["puuid"]}, {"puuid": opponents[2]["puuid"]}, 50),
    ]

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["first_deaths"] is None


def test_first_death_is_null_when_a_player_death_has_no_timestamp():
    raw = sample_match()
    me = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    opponents = [p for p in raw["players"] if p["team_id"] != me["team_id"]]
    raw["kills"] = [_event(1, {"puuid": opponents[0]["puuid"]}, {"puuid": me["puuid"]}, None)]

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["first_deaths"] is None


def test_overtime_side_splits_follow_each_round_role_after_side_switch():
    raw = sample_match()
    me = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    raw["rounds"] = [
        {
            "id": number,
            "winning_team": me["team_id"],
            "winning_team_role": "Attacker" if number < 24 else "Defender",
        }
        for number in range(30)
    ]

    row, _ = flatten_match(raw, "TestPlayer", "TEST")

    assert row["attack_rounds_played"] == 24
    assert row["defense_rounds_played"] == 6
    assert row["attack_rounds_won"] == 24
    assert row["defense_rounds_won"] == 6


def test_malformed_kill_events_do_not_crash_flattening():
    raw = sample_match()
    me = next(p for p in raw["players"] if p["name"] == "TestPlayer")
    raw["kills"] = [
        None,
        {"round": "bad", "killer": {"puuid": me["puuid"]}},
        {"round": 1, "killer": None, "victim": {"puuid": me["puuid"]}},
    ]

    row, kill_rows = flatten_match(raw, "TestPlayer", "TEST", include_rounds=True)

    assert row["first_deaths"] is None
    assert row["multikills_2k"] == 0
    assert len(kill_rows) == 2

