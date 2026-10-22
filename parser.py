"""Tolerant parsing and flattening for HenrikDev Valorant v4 match objects."""

from __future__ import annotations

from collections import Counter
from typing import Any


PERFORMANCE_COMPONENTS = (
    "damage", "trades", "assists", "utility_usage", "plants", "defuses", "adjusted_kills", "adjusted_deaths"
)


def _get(value: Any, *path: str, default: Any = None) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _float(value: Any) -> float | None:
    try:
        result = float(value) if value is not None else None
        return result if result is None or result == result and abs(result) != float("inf") else None
    except (ValueError, TypeError, OverflowError):
        return None


def _id(value: Any) -> Any:
    return value.get("puuid") if isinstance(value, dict) else None


def _player_hs_pct(player: dict[str, Any]) -> float | None:
    stats = player.get("stats") or {}
    head = _int(stats.get("headshots"))
    total = sum(_int(stats.get(k)) or 0 for k in ("headshots", "bodyshots", "legshots"))
    return head * 100 / total if head is not None and total else None


def _kd(player: dict[str, Any]) -> float | None:
    stats = player.get("stats") or {}
    kills, deaths = _int(stats.get("kills")), _int(stats.get("deaths"))
    return kills / deaths if kills is not None and deaths else None


def _ps(player: dict[str, Any]) -> float | None:
    # Verified v4 path: players[].performance.score.
    return _float(_get(player, "performance", "score"))


def _ranks(players: list[dict[str, Any]], team: str | None = None) -> dict[Any, int]:
    eligible = [p for p in players if _ps(p) is not None and (team is None or p.get("team_id") == team)]
    eligible.sort(key=lambda p: (-_ps(p), str(p.get("puuid", ""))))
    ranks: dict[Any, int] = {}
    last_score: float | None = None
    last_rank = 0
    for index, player in enumerate(eligible, 1):
        score = _ps(player)
        if score != last_score:
            last_rank = index
            last_score = score
        ranks[player.get("puuid")] = last_rank
    return ranks


def _round_roles(match: dict[str, Any]) -> dict[int, dict[str, str]]:
    roles: dict[int, dict[str, str]] = {}
    for fallback, rnd in enumerate(match.get("rounds") or [], 1):
        if not isinstance(rnd, dict):
            continue
        number = _int(rnd.get("id"))
        if number is None:
            number = fallback - 1
        winner, role = rnd.get("winning_team"), rnd.get("winning_team_role")
        if winner not in ("Blue", "Red") or role not in ("Attacker", "Defender"):
            continue
        other = "Defender" if role == "Attacker" else "Attacker"
        roles[number] = {winner: role, ("Red" if winner == "Blue" else "Blue"): other}
    return roles


def _average(
    players: list[dict[str, Any]], metric: str, derived: dict[Any, Counter[str]], has_rounds: bool, has_kills: bool
) -> float | None:
    vals: list[float] = []
    for player in players:
        if metric in {
            "first_bloods", "first_deaths", "plants", "defuses", "multikills_2k", "multikills_3k",
            "multikills_4k", "multikills_5k",
        }:
            round_based = metric in {"first_bloods", "plants", "defuses"}
            val = None if (round_based and not has_rounds) or (not round_based and not has_kills) else float(
                derived.get(player.get("puuid"), Counter())[metric]
            )
        elif metric == "perf_score":
            val = _ps(player)
        elif metric == "kd":
            val = _kd(player)
        elif metric == "adr":
            val = _float(_get(player, "performance", "breakdown", "damage"))
        elif metric == "hs_pct":
            val = _player_hs_pct(player)
        elif metric == "damage_dealt":
            val = _float(_get(player, "stats", "damage", "dealt"))
        elif metric == "damage_received":
            val = _float(_get(player, "stats", "damage", "received"))
        elif metric in ("spent_total", "spent_avg", "loadout_avg"):
            paths = {
                "spent_total": ("spent", "overall"),
                "spent_avg": ("spent", "average"),
                "loadout_avg": ("loadout_value", "average"),
            }
            val = _float(_get(player, "economy", *paths[metric]))
        else:
            val = _float(_get(player, "stats", metric))
        if val is not None:
            vals.append(val)
    return round(sum(vals) / len(vals), 2) if vals else None


def flatten_match(
    raw: dict[str, Any], name: str, tag: str, include_rounds: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a player-only match row and optional kill rows; absent data stays None."""
    if isinstance(raw.get("data"), list):
        if not raw["data"]:
            raise ValueError("Match response contains an empty data array")
        raw = raw["data"][0]

    players = [p for p in (raw.get("players") or []) if isinstance(p, dict)]
    me = next((p for p in players if str(p.get("name", "")).casefold() == name.casefold()
               and str(p.get("tag", "")).casefold() == tag.casefold()), None)
    match_id = _get(raw, "metadata", "match_id")
    if me is None:
        raise ValueError(f"Player {name}#{tag} was not present in match {match_id}.")

    metadata = raw.get("metadata") or {}
    stats = me.get("stats") or {}
    mine = me.get("team_id")
    my_id = me.get("puuid")
    teams = [team for team in (raw.get("teams") or []) if isinstance(team, dict)]
    own_team = next((team for team in teams if team.get("team_id") == mine), {})
    enemy_team = next((team for team in teams if team.get("team_id") != mine), {})
    own_players = [p for p in players if p.get("team_id") == mine]
    enemy_players = [p for p in players if p.get("team_id") != mine]
    overall_ranks = _ranks(players)
    own_ranks = _ranks(players, mine)
    roles = _round_roles(raw)
    attack_played = defense_played = attack_won = defense_won = 0
    for rnd in raw.get("rounds") or []:
        if not isinstance(rnd, dict):
            continue
        number = _int(rnd.get("id"))
        side = roles.get(number, {}).get(mine) if number is not None else None
        if side == "Attacker":
            attack_played += 1
            attack_won += rnd.get("winning_team") == mine
        elif side == "Defender":
            defense_played += 1
            defense_won += rnd.get("winning_team") == mine

    headshots = _int(stats.get("headshots"))
    economy = me.get("economy") or {}
    performance = me.get("performance") or {}
    breakdown = performance.get("breakdown") or {}
    kill_events = [event for event in (raw.get("kills") or []) if isinstance(event, dict)]
    has_round_data = isinstance(raw.get("rounds"), list)
    has_kill_data = isinstance(raw.get("kills"), list)
    derived: dict[Any, Counter[str]] = {p["puuid"]: Counter() for p in players if p.get("puuid") is not None}
    my_kills_by_round: Counter[int] = Counter()
    kills_by_player_round: Counter[tuple[Any, int]] = Counter()
    first_kill_by_round: dict[int, tuple[int | None, Any]] = {}
    for rnd in raw.get("rounds") or []:
        if not isinstance(rnd, dict):
            continue
        first_id = _id(rnd.get("first_blood"))
        plant_id = _id(_get(rnd, "plant", "player"))
        defuse_id = _id(_get(rnd, "defuse", "player"))
        if first_id in derived:
            derived[first_id]["first_bloods"] += 1
        if plant_id in derived:
            derived[plant_id]["plants"] += 1
        if defuse_id in derived:
            derived[defuse_id]["defuses"] += 1
    for event in kill_events:
        number = _int(event.get("round"))
        if number is None:
            continue
        killer, victim = _id(event.get("killer")), _id(event.get("victim"))
        if killer in derived:
            kills_by_player_round[(killer, number)] += 1
            if killer == my_id:
                my_kills_by_round[number] += 1
        moment = _int(event.get("time_in_round_in_ms"))
        old = first_kill_by_round.get(number)
        if old is None or (moment is not None and (old[0] is None or moment < old[0])):
            first_kill_by_round[number] = (moment, victim)
    for (killer, _), count in kills_by_player_round.items():
        if count in (2, 3, 4, 5):
            derived[killer][f"multikills_{count}k"] += 1
    for _, victim in first_kill_by_round.values():
        if victim in derived:
            derived[victim]["first_deaths"] += 1

    side_stats: dict[str, Any] = {}
    for label, group in (("team", own_players), ("enemy", enemy_players)):
        for metric in (
            "perf_score", "kills", "deaths", "assists", "kd", "damage_dealt", "damage_received", "adr", "hs_pct",
            "headshots", "bodyshots", "legshots", "first_bloods", "first_deaths", "plants", "defuses",
            "multikills_2k", "multikills_3k", "multikills_4k", "multikills_5k", "spent_total", "spent_avg",
            "loadout_avg",
        ):
            side_stats[f"{label}_avg_{metric}"] = _average(group, metric, derived, has_round_data, has_kill_data)

    first_bloods = derived.get(my_id, Counter())["first_bloods"] if has_round_data else None
    first_deaths = derived.get(my_id, Counter())["first_deaths"] if has_kill_data else None
    plants = derived.get(my_id, Counter())["plants"] if has_round_data else None
    defuses = derived.get(my_id, Counter())["defuses"] if has_round_data else None
    own_party = me.get("party_id")

    row: dict[str, Any] = {
        "match_id": match_id,
        "started_at": metadata.get("started_at"),
        "map": _get(metadata, "map", "name"),
        "queue": _get(metadata, "queue", "id"),
        "mode": _get(metadata, "queue", "id"),
        "season": _get(metadata, "season", "short"),
        "game_length_s": round(_int(metadata.get("game_length_in_ms")) / 1000, 3)
        if _int(metadata.get("game_length_in_ms")) is not None else None,
        "won": own_team.get("won"),
        "rounds_won": _int(_get(own_team, "rounds", "won")),
        "rounds_lost": _int(_get(own_team, "rounds", "lost")),
        "attack_rounds_won": int(attack_won) if attack_played else None,
        "attack_rounds_played": attack_played or None,
        "defense_rounds_won": int(defense_won) if defense_played else None,
        "defense_rounds_played": defense_played or None,
        "agent": _get(me, "agent", "name"),
        "kills": _int(stats.get("kills")),
        "deaths": _int(stats.get("deaths")),
        "assists": _int(stats.get("assists")),
        "kd": _kd(me),
        "damage_dealt": _int(_get(stats, "damage", "dealt")),
        "damage_received": _int(_get(stats, "damage", "received")),
        "adr": _float(_get(performance, "breakdown", "damage")),
        "hs_pct": round(_player_hs_pct(me), 2) if _player_hs_pct(me) is not None else None,
        "headshots": headshots,
        "bodyshots": _int(stats.get("bodyshots")),
        "legshots": _int(stats.get("legshots")),
        "first_bloods": int(first_bloods),
        "first_deaths": int(first_deaths),
        "plants": int(plants),
        "defuses": int(defuses),
        "multikills_2k": derived.get(my_id, Counter())["multikills_2k"] if has_kill_data else None,
        "multikills_3k": derived.get(my_id, Counter())["multikills_3k"] if has_kill_data else None,
        "multikills_4k": derived.get(my_id, Counter())["multikills_4k"] if has_kill_data else None,
        "multikills_5k": derived.get(my_id, Counter())["multikills_5k"] if has_kill_data else None,
        "clutches": None,
        "spent_total": _int(_get(economy, "spent", "overall")),
        "spent_avg": _float(_get(economy, "spent", "average")),
        "loadout_avg": _float(_get(economy, "loadout_value", "average")),
        "perf_score": _ps(me),
        "perf_rank_in_match": overall_ranks.get(my_id),
        "perf_rank_in_team": own_ranks.get(my_id),
        "rank_tier": _get(me, "tier", "name"),
        "rr_change": None,
        "team_placement": _int(own_team.get("placement")),
        "party_size": sum(p.get("party_id") == own_party for p in players) if own_party else None,
        "ps_component_damage": _float(breakdown.get("damage")),
        "ps_component_trades": _float(breakdown.get("trades")),
        "ps_component_assists": _float(breakdown.get("assists")),
        "ps_component_utility_usage": _float(breakdown.get("utility_usage")),
        "ps_component_plants": _float(breakdown.get("plants")),
        "ps_component_defuses": _float(breakdown.get("defuses")),
        "ps_component_adjusted_kills": _float(breakdown.get("adjusted_kills")),
        "ps_component_adjusted_deaths": _float(breakdown.get("adjusted_deaths")),
    }
    row.update(side_stats)

    agents = {p.get("puuid"): _get(p, "agent", "name") for p in players}
    kill_rows: list[dict[str, Any]] = []
    if include_rounds:
        for event in kill_events:
            killer, victim = event.get("killer") or {}, event.get("victim") or {}
            number = _int(event.get("round"))
            role = roles.get(number, {}).get(killer.get("team")) if number is not None else None
            kill_rows.append({
                "match_id": match_id,
                "round": number,
                "time_in_round_ms": _int(event.get("time_in_round_in_ms")),
                "killer_is_me": _id(killer) == my_id if _id(killer) is not None else None,
                "victim_is_me": _id(victim) == my_id if _id(victim) is not None else None,
                "killer_agent": agents.get(_id(killer)),
                "victim_agent": agents.get(_id(victim)),
                "weapon": _get(event, "weapon", "name"),
                "headshot": None,
                "assist_is_me": (
                    any(_id(a) == my_id for a in event["assistants"])
                    if isinstance(event.get("assistants"), list) else None
                ),
                "side": role,
            })
    return row, kill_rows

