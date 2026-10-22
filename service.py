"""Reusable fetch and export workflow called by the CLI frontend."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api import APIError, HenrikDevClient
from exporter import write_digest, write_match_exports
from parser import flatten_match


PAGE_SIZE = 5


@dataclass(frozen=True)
class FetchOptions:
    name: str
    tag: str
    region: str = "ap"
    act: str = "current"
    mode: str | None = None
    limit: int = 100
    rounds: bool = False
    export_csv: bool = False
    export_jsonl: bool = False
    export_digest: bool = False
    data_dir: Path = Path("data")


@dataclass(frozen=True)
class FetchResult:
    match_count: int
    act: str
    null_performance_scores: int | None
    kill_count: int
    digest_paths: tuple[Path, ...]


def _unwrap(raw: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw.get("data"), list):
        if not raw["data"] or not isinstance(raw["data"][0], dict):
            raise APIError("Cached HenrikDev response has no match object.")
        return raw["data"][0]
    return raw


def _load_cache(raw_dir: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    if not raw_dir.exists():
        return cache
    for path in raw_dir.glob("*.json"):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise APIError(f"Cannot read raw cache {path}: {exc}") from exc
        match = _unwrap(parsed)
        metadata = match.get("metadata")
        match_id = metadata.get("match_id") if isinstance(metadata, dict) else None
        if not match_id:
            raise APIError(f"Raw cache file {path} has no metadata.match_id.")
        if path.stem != str(match_id):
            raise APIError(f"Raw cache filename {path.name} does not match match ID {match_id}.")
        cache[str(match_id)] = match
    return cache


def _selected_matches(
    cache: dict[str, dict[str, Any]], name: str, tag: str, act: str, mode: str | None
) -> list[dict[str, Any]]:
    selected = []
    for match in cache.values():
        metadata = match.get("metadata") or {}
        if str((metadata.get("season") or {}).get("short") or "") != act:
            continue
        if mode and str((metadata.get("queue") or {}).get("id") or "").casefold() != mode.casefold():
            continue
        players = match.get("players") or []
        if not any(
            str(player.get("name", "")).casefold() == name.casefold()
            and str(player.get("tag", "")).casefold() == tag.casefold()
            for player in players if isinstance(player, dict)
        ):
            raise APIError(f"Cached match {metadata.get('match_id')} does not contain {name}#{tag}.")
        selected.append(match)
    return selected


def run_fetch(options: FetchOptions, api_key: str | None = None) -> FetchResult:
    """Execute the workflow and return structured results without CLI output."""
    if options.limit <= 0:
        raise APIError("--limit must be a positive integer.")

    raw_dir = options.data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache = _load_cache(raw_dir)
    initially_cached_ids = set(cache)
    client = HenrikDevClient(api_key=api_key)
    target_act: str | None = None if options.act.casefold() == "current" else options.act
    prefetched_response: dict[str, Any] | None = None

    if target_act is None:
        latest = client.get_matches_page(options.name, options.tag, options.region, None, 0, 1)
        latest_matches = latest.get("data")
        if not isinstance(latest_matches, list):
            raise APIError("HenrikDev response has no match list at `data`.")
        if not latest_matches or not isinstance(latest_matches[0], dict):
            raise APIError("HenrikDev returned no matches; there is no current act to select.")
        latest_meta = latest_matches[0].get("metadata") or {}
        target_act = (latest_meta.get("season") or {}).get("short")
        if not target_act:
            raise APIError("Newest match is missing metadata.season.short; cannot determine the current act.")
        target_act = str(target_act)
        if options.mode is None:
            prefetched_response = latest

    entered_target = False
    offset = 0
    crossed_act = False
    cached_boundary = False
    selected_count_before_fetch: int | None = None

    while True:
        if prefetched_response is not None:
            response = prefetched_response
            prefetched_response = None
        else:
            request_size = min(PAGE_SIZE, options.limit)
            response = client.get_matches_page(
                options.name, options.tag, options.region, options.mode, offset, request_size
            )
        matches = response.get("data")
        if not isinstance(matches, list):
            raise APIError("HenrikDev response has no match list at `data`.")
        if not matches:
            break
        for match in matches:
            if not isinstance(match, dict):
                raise APIError("HenrikDev returned a malformed match entry.")
            metadata = match.get("metadata") or {}
            match_id = metadata.get("match_id")
            match_act = (metadata.get("season") or {}).get("short")
            if not match_id:
                raise APIError("A match response is missing metadata.match_id.")
            if not match_act:
                raise APIError(f"Match {match_id} is missing metadata.season.short; cannot apply --act safely.")
            if target_act is None:
                target_act = str(match_act)
                selected_count_before_fetch = len(
                    _selected_matches(cache, options.name, options.tag, target_act, options.mode)
                )
            if str(match_act) != target_act:
                if entered_target:
                    crossed_act = True
                    break
                continue

            if selected_count_before_fetch is None:
                selected_count_before_fetch = len(
                    _selected_matches(cache, options.name, options.tag, target_act, options.mode)
                )
            entered_target = True
            if options.mode and str((metadata.get("queue") or {}).get("id") or "").casefold() != options.mode.casefold():
                continue
            players = match.get("players") or []
            if not any(
                str(player.get("name", "")).casefold() == options.name.casefold()
                and str(player.get("tag", "")).casefold() == options.tag.casefold()
                for player in players if isinstance(player, dict)
            ):
                raise APIError(f"Match {match_id} returned without player {options.name}#{options.tag}.")

            match_id = str(match_id)
            if match_id in initially_cached_ids:
                cached_boundary = True
            if match_id not in cache:
                cache_path = raw_dir / f"{match_id}.json"
                cache_path.write_text(
                    json.dumps(match, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
                )
                cache[match_id] = match

        offset += len(matches)
        if crossed_act:
            break
        if target_act is not None:
            cached_for_request = _selected_matches(
                cache, options.name, options.tag, target_act, options.mode
            )
            if len(cached_for_request) >= options.limit and (
                cached_boundary or (selected_count_before_fetch is not None and selected_count_before_fetch < options.limit)
            ):
                break

    if target_act is None:
        raise APIError("HenrikDev returned no matches; there is no current act to select.")
    act_matches = _selected_matches(cache, options.name, options.tag, target_act, options.mode)
    if not act_matches:
        suffix = f" and mode {options.mode}" if options.mode else ""
        raise APIError(f"No matches found for act {target_act}{suffix}.")
    act_matches.sort(key=lambda match: (match.get("metadata", {}).get("started_at") or ""), reverse=True)
    act_matches = act_matches[:options.limit]

    wants_flattened_data = options.rounds or options.export_csv or options.export_jsonl or options.export_digest
    rows: list[dict[str, Any]] = []
    kill_rows: list[dict[str, Any]] = []
    if wants_flattened_data:
        for match in act_matches:
            row, match_kills = flatten_match(match, options.name, options.tag, options.rounds)
            rows.append(row)
            kill_rows.extend(match_kills)

    write_match_exports(
        options.data_dir,
        rows,
        kill_rows,
        include_csv=options.export_csv,
        include_jsonl=options.export_jsonl,
        include_rounds=options.rounds,
    )
    digest_paths: list[Path] = []
    if options.export_digest:
        modes = sorted({str(row["queue"]) for row in rows if row.get("queue")})
        digest_paths = write_digest(
            options.data_dir, rows, f"{options.name}#{options.tag}", options.region, target_act, modes or None
        )
    return FetchResult(
        match_count=len(act_matches),
        act=target_act,
        null_performance_scores=(
            sum(row.get("perf_score") is None for row in rows) if wants_flattened_data else None
        ),
        kill_count=len(kill_rows),
        digest_paths=tuple(digest_paths),
    )
