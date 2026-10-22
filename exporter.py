"""JSONL, CSV, kill CSV, and token-aware Markdown export."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from parser import SCHEMA_VERSION


CORE_FIELDS = [
    "schema_version", "match_id", "started_at", "map", "queue", "mode", "season", "game_length_s", "won", "result",
    "rounds_won", "rounds_lost", "round_records_total", "rounds_with_valid_result",
    "round_validation_warning", "attack_rounds_won", "attack_rounds_played", "defense_rounds_won",
    "defense_rounds_played", "agent", "kills", "deaths", "assists", "kd", "damage_dealt",
    "damage_received", "adr", "hs_pct", "headshots", "bodyshots", "legshots", "first_bloods",
    "first_deaths", "plants", "defuses", "multikills_2k", "multikills_3k", "multikills_4k",
    "multikills_5k", "clutches", "spent_total", "spent_avg", "loadout_avg", "perf_score",
    "perf_rank_in_match", "perf_rank_in_team", "rank_tier", "rr_change", "team_placement", "party_size",
    "ps_component_damage", "ps_component_trades", "ps_component_assists", "ps_component_utility_usage",
    "ps_component_plants", "ps_component_defuses", "ps_component_adjusted_kills",
    "ps_component_adjusted_deaths",
]
CONTEXT_METRICS = [
    "perf_score", "kills", "deaths", "assists", "kd", "damage_dealt", "damage_received", "adr", "hs_pct",
    "headshots", "bodyshots", "legshots", "first_bloods", "first_deaths", "plants", "defuses",
    "multikills_2k", "multikills_3k", "multikills_4k", "multikills_5k", "spent_total", "spent_avg", "loadout_avg",
]
CORE_FIELDS += [f"{side}_avg_{metric}" for side in ("team", "enemy") for metric in CONTEXT_METRICS]
MATCH_FIELDS = CORE_FIELDS
KILL_FIELDS = [
    "match_id", "round", "time_in_round_ms", "killer_is_me", "victim_is_me", "killer_agent",
    "victim_agent", "weapon", "headshot", "assist_is_me", "side",
]
SHORT_FIELDS = [
    ("date_utc", "started_at"), ("map", "map"), ("mode", "queue"), ("agent", "agent"),
    ("Result", "result"), ("K", "kills"), ("D", "deaths"), ("A", "assists"), ("K/D", "kd"),
    ("ADR", "adr"), ("HS%", "hs_pct"), ("PS", "perf_score"), ("PS#", "perf_rank_in_match"),
    ("Team#", "perf_rank_in_team"), ("Rank", "rank_tier"), ("FB", "first_bloods"), ("FD", "first_deaths"),
    ("ATK+", "attack_rounds_won"), ("ATK", "attack_rounds_played"), ("DEF+", "defense_rounds_won"),
    ("DEF", "defense_rounds_played"), ("TeamPS", "team_avg_perf_score"),
    ("EnemyPS", "enemy_avg_perf_score"), ("Party", "party_size"), ("R+", "rounds_won"),
    ("R-", "rounds_lost"), ("match_id", "match_id"),
]
MAX_DIGEST_CHARS = 120_000  # approximately 30k tokens at four characters per token.


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace one output only after its complete contents have been written."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def write_match_exports(
    output_dir: Path,
    rows: list[dict[str, Any]],
    kills: list[dict[str, Any]],
    include_rounds: bool = False,
    include_csv: bool = False,
    include_jsonl: bool = False,
) -> None:
    if not (include_csv or include_jsonl or include_rounds):
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda row: row.get("started_at") or "", reverse=True)
    if include_jsonl:
        content = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
            for row in rows
        )
        _atomic_write_text(output_dir / "matches.jsonl", content)
    if include_csv:
        handle = io.StringIO(newline="")
        writer = csv.DictWriter(handle, fieldnames=MATCH_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        _atomic_write_text(output_dir / "matches.csv", handle.getvalue())
    if include_rounds:
        handle = io.StringIO(newline="")
        writer = csv.DictWriter(handle, fieldnames=KILL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kills)
        _atomic_write_text(output_dir / "kills.csv", handle.getvalue())


def _avg(values: Iterable[Any]) -> float | None:
    nums = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return sum(nums) / len(nums) if nums else None


def _rate(wins: int, total: int) -> float | None:
    return 100.0 * wins / total if total else None


def _format(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "W" if value else "L"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]], digits: int = 1) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_format(value, digits) for value in row) + " |" for row in rows)
    return lines


def _group_table(rows: list[dict[str, Any]], field: str) -> list[list[Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "Unknown")].append(row)
    result = []
    for key, games in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        results = [row.get("result") for row in games]
        wins = sum(value == "win" for value in results)
        losses = sum(value == "loss" for value in results)
        draws = sum(value == "draw" for value in results)
        classified = wins + losses + draws
        decisive = wins + losses
        unknown = len(games) - classified
        result.append([
            key, len(games), wins, losses, draws, unknown, _rate(wins, classified), _rate(wins, decisive),
            _avg(row.get("kd") for row in games),
            _avg(row.get("adr") for row in games), _avg(row.get("hs_pct") for row in games),
            _avg(row.get("perf_score") for row in games),
        ])
    return result


def _aggregate_lines(rows: list[dict[str, Any]]) -> list[str]:
    games = len(rows)
    results = [row.get("result") for row in rows]
    wins = sum(value == "win" for value in results)
    losses = sum(value == "loss" for value in results)
    draws = sum(value == "draw" for value in results)
    classified = wins + losses + draws
    decisive = wins + losses
    unknown = games - classified
    rounds = sum((row.get("rounds_won") or 0) + (row.get("rounds_lost") or 0) for row in rows)
    fb = sum(row.get("first_bloods") or 0 for row in rows)
    fd = sum(row.get("first_deaths") or 0 for row in rows)
    mvp = sum(row.get("perf_rank_in_match") == 1 for row in rows)
    lines = ["## Aggregates", "", "### Overall"]
    lines += _table(
        ["matches", "wins", "losses", "draws", "unknown", "win_rate_classified_%", "win_rate_decisive_%",
         "avg_K", "avg_D", "avg_A", "avg_K/D", "avg_ADR", "avg_HS_%",
         "FB/round", "FD/round", "avg_PS", "MVP_rate_%"],
        [[games, wins, losses, draws, unknown, _rate(wins, classified), _rate(wins, decisive),
          _avg(r.get("kills") for r in rows), _avg(r.get("deaths") for r in rows),
          _avg(r.get("assists") for r in rows), _avg(r.get("kd") for r in rows), _avg(r.get("adr") for r in rows),
          _avg(r.get("hs_pct") for r in rows), fb / rounds if rounds else None, fd / rounds if rounds else None,
          _avg(r.get("perf_score") for r in rows), _rate(mvp, games)]],
    )
    lines.append(
        "Win rates exclude unknown results: classified = wins / (wins + losses + draws); "
        "decisive = wins / (wins + losses), excluding draws."
    )
    for title, field in (("By agent", "agent"), ("By map", "map"), ("By mode", "queue")):
        lines.extend(["", f"### {title}"])
        lines += _table([field, "games", "wins", "losses", "draws", "unknown", "win_rate_classified_%",
                         "win_rate_decisive_%", "avg_K/D", "avg_ADR", "avg_HS_%", "avg_PS"],
                        _group_table(rows, field))
        if field == "map":
            map_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                map_groups[str(row.get("map") or "Unknown")].append(row)
            side_rows = []
            for map_name, items in sorted(map_groups.items()):
                attack_played = sum(row.get("attack_rounds_played") or 0 for row in items)
                defense_played = sum(row.get("defense_rounds_played") or 0 for row in items)
                attack_won = sum(row.get("attack_rounds_won") or 0 for row in items)
                defense_won = sum(row.get("defense_rounds_won") or 0 for row in items)
                side_rows.append([map_name, _rate(attack_won, attack_played), _rate(defense_won, defense_played)])
            lines.extend(["", "Side rates use available round role metadata."])
            lines += _table(["map", "attack_win_rate_%", "defense_win_rate_%"], side_rows)

    chronological = list(reversed(rows))
    trend = []
    for i, current in enumerate(chronological):
        window = chronological[max(0, i - 9):i + 1]
        n = len(window)
        trend.append([
            current.get("started_at"), n, _avg(r.get("perf_score") for r in window),
            _avg(r.get("kd") for r in window), _avg(r.get("adr") for r in window),
            _rate(
                sum(r.get("result") == "win" for r in window),
                sum(r.get("result") in ("win", "loss", "draw") for r in window),
            ),
        ])
    trend.reverse()
    lines.extend(["", "### Trailing 10-match trend (newest first; partial windows at start)"])
    lines += _table(["started_at_utc", "window_n", "PS_10", "K/D_10", "ADR_10", "win_rate_%_10"], trend)
    ranked = [row for row in rows if isinstance(row.get("perf_score"), (int, float))]
    best = sorted(ranked, key=lambda row: row["perf_score"], reverse=True)[:5]
    worst = sorted(ranked, key=lambda row: row["perf_score"])[:5]
    lines.extend(["", "### Performance Score outliers"])
    lines += _table(["set", "started_at_utc", "PS", "agent", "map", "match_id"], [
        [label, row.get("started_at"), row.get("perf_score"), row.get("agent"), row.get("map"), row.get("match_id")]
        for label, subset in (("best", best), ("worst", worst)) for row in subset
    ])
    warnings = [row for row in rows if row.get("round_validation_warning")]
    unchecked = sum(
        row.get("round_validation_warning") is None
        and not (
            row.get("round_records_total") is not None
            and row.get("rounds_won") is not None
            and row.get("rounds_lost") is not None
        )
        for row in rows
    )
    lines.extend(["", "### Round-data validation"])
    if warnings:
        lines += _table(["match_id", "round_records_total", "rounds_with_valid_result", "warning"], [
            [row.get("match_id"), row.get("round_records_total"), row.get("rounds_with_valid_result"),
             row.get("round_validation_warning")]
            for row in warnings
        ])
    else:
        lines.append("No mismatch among comparable records.")
    if unchecked:
        lines.append(f"{unchecked} match(es) lacked enough round data to compare.")
    return lines


def _dictionary_lines() -> list[str]:
    descriptions = {
        "schema_version": "processed export contract version",
        "match_id": "HenrikDev match identifier", "started_at": "UTC ISO-8601 timestamp", "map": "map name",
        "queue": "HenrikDev queue ID", "mode": "queue ID alias", "season": "season.short act code",
        "game_length_s": "seconds", "won": "provider Boolean; false may include a draw",
        "result": "normalized win/loss/draw/unknown; round totals take precedence, ambiguous false is unknown",
        "rounds_won": "provider-reported rounds won", "rounds_lost": "provider-reported rounds lost",
        "round_records_total": "number of entries in the raw rounds array; null if absent or malformed",
        "rounds_with_valid_result": "round records whose winning_team is Blue or Red; null if rounds is absent or malformed",
        "round_validation_warning": "warning when recognized round winners disagree with provider team totals",
        "attack_rounds_won": "attack rounds won", "attack_rounds_played": "attack rounds observed",
        "defense_rounds_won": "defense rounds won", "defense_rounds_played": "defense rounds observed",
        "agent": "agent name", "kills": "kills", "deaths": "deaths", "assists": "assists", "kd": "kills/deaths",
        "damage_dealt": "damage points", "damage_received": "damage points", "adr": "API performance.breakdown.damage (per-round damage)",
        "hs_pct": "headshots / (headshots + bodyshots + legshots), percent", "headshots": "hit events",
        "bodyshots": "hit events", "legshots": "hit events", "first_bloods": "rounds", "first_deaths": "rounds",
        "plants": "spike plant rounds", "defuses": "spike defuse rounds", "multikills_2k": "rounds with exactly 2 kills",
        "multikills_3k": "rounds with exactly 3 kills", "multikills_4k": "rounds with exactly 4 kills",
        "multikills_5k": "rounds with exactly 5 kills", "clutches": "unavailable in response; null",
        "spent_total": "credits", "spent_avg": "credits/round", "loadout_avg": "credits/round",
        "perf_score": "HenrikDev performance.score; 0–500; formula undisclosed; not an RR/MMR change; compare within a match",
        "perf_rank_in_match": "rank among match participants by perf_score, 1=highest", "perf_rank_in_team": "rank among teammates by perf_score, 1=highest",
        "rank_tier": "rank tier at match time", "rr_change": "RR change; unavailable in match payload, null",
        "team_placement": "team placement field", "party_size": "players sharing party ID in match",
        "ps_component_damage": "API performance.breakdown.damage (per-round value)",
        "ps_component_trades": "API performance.breakdown.trades (per-round value)",
        "ps_component_assists": "API performance.breakdown.assists (per-round value)",
        "ps_component_utility_usage": "API performance.breakdown.utility_usage (per-round value)",
        "ps_component_plants": "API performance.breakdown.plants (per-round value)",
        "ps_component_defuses": "API performance.breakdown.defuses (per-round value)",
        "ps_component_adjusted_kills": "API performance.breakdown.adjusted_kills (per-round value)",
        "ps_component_adjusted_deaths": "API performance.breakdown.adjusted_deaths (per-round value)",
    }
    for side in ("team", "enemy"):
        subject = "teammate" if side == "team" else "enemy"
        for metric in CONTEXT_METRICS:
            descriptions[f"{side}_avg_{metric}"] = f"mean {subject} {metric}; same units as {metric}"
    lines = ["## Data dictionary", "", *[f"- `{field}`: {descriptions.get(field, 'value')}" for field in MATCH_FIELDS]]
    aliases = {
        "date_utc": "started_at", "map": "map", "mode": "queue", "agent": "agent", "Result": "result",
        "K": "kills", "D": "deaths", "A": "assists",
        "K/D": "kd", "ADR": "adr", "HS%": "hs_pct", "PS": "perf_score", "PS#": "perf_rank_in_match",
        "Team#": "perf_rank_in_team", "ATK+": "attack_rounds_won", "ATK": "attack_rounds_played",
        "DEF+": "defense_rounds_won", "DEF": "defense_rounds_played", "TeamPS": "team_avg_perf_score",
        "EnemyPS": "enemy_avg_perf_score", "Party": "party_size", "R+": "rounds_won", "R-": "rounds_lost",
        "FB": "first_bloods", "FD": "first_deaths", "match_id": "match_id",
    }
    lines.extend(["", "Per-match table abbreviations:"])
    lines.extend(f"- `{short}`: `{field}`" for short, field in SHORT_FIELDS if short in aliases)
    lines.extend([
        "", "Performance Score is 0–500. Riot has not published its formula; compare values with players in the same match.",
        "Performance Score is not an RR/MMR change. Riot states it does not influence RR gain/loss; the HenrikDev match response has no RR-change field.",
    ])
    return lines


def write_digest(
    output_dir: Path, rows: list[dict[str, Any]], player: str, region: str, act: str,
    modes: list[str] | None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda row: row.get("started_at") or "", reverse=True)
    dates = [row.get("started_at") for row in rows if row.get("started_at")]
    date_range = f"{min(dates)} — {max(dates)}" if dates else "unknown"
    mode_text = ", ".join(modes) if modes else "all"
    null_ps = sum(row.get("perf_score") is None for row in rows)
    header = [
        f"# Valorant match digest: {player}", "", f"- Region: {region}", f"- Act: {act}",
        f"- Date range (UTC): {date_range}", f"- Match count: {len(rows)}", f"- Modes included: {mode_text}",
        f"- Processed schema: {SCHEMA_VERSION}", "- Source: raw HenrikDev match response; derived metrics follow the README definitions.",
        f"- Matches with null Performance Score: {null_ps}",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
    ]
    dictionary = _dictionary_lines()
    aggregates = _aggregate_lines(rows)
    table_header = ["## Per-match (newest first)", "", "| " + " | ".join(label for label, _ in SHORT_FIELDS) + " |",
                    "| " + " | ".join("---" for _ in SHORT_FIELDS) + " |"]
    match_lines = ["| " + " | ".join(_format(row.get(key)) for _, key in SHORT_FIELDS) + " |" for row in rows]
    prefix = header + dictionary + [""] + aggregates + [""] + table_header
    full = prefix + match_lines
    if sum(len(line) + 1 for line in full) <= MAX_DIGEST_CHARS:
        path = output_dir / "llm_digest.md"
        _atomic_write_text(path, "\n".join(full) + "\n")
        for stale in output_dir.glob("llm_digest_part*.md"):
            stale.unlink()
        return [path]

    continuation_header = [
        f"# Valorant match digest: {player} (continued)", "", f"- Act: {act}",
        "", "## Per-match (newest first)", "", table_header[2], table_header[3],
    ]
    chunks: list[list[str]] = []
    current = list(prefix)
    length = sum(len(line) + 1 for line in current)
    for line in match_lines:
        if length + len(line) + 1 > MAX_DIGEST_CHARS and len(current) > len(prefix):
            chunks.append(current)
            current = list(continuation_header)
            length = sum(len(item) + 1 for item in current)
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append(current)
    paths = []
    for index, chunk in enumerate(chunks, 1):
        path = output_dir / f"llm_digest_part{index}.md"
        _atomic_write_text(path, "\n".join(chunk) + "\n")
        paths.append(path)
    for stale in output_dir.glob("llm_digest_part*.md"):
        if stale not in paths:
            stale.unlink()
    (output_dir / "llm_digest.md").unlink(missing_ok=True)
    return paths


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "fetch_manifest.json"
    _atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return path

