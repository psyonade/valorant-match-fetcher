"""Reusable fetch and export workflow called by the CLI frontend."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from api import APIError, HenrikDevClient
from exporter import write_digest, write_manifest, write_match_exports
from parser import SCHEMA_VERSION, flatten_match


PAGE_SIZE = 5


class FetchCancelled(APIError):
    """Cooperative cancellation requested between API pages."""


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
    export_manifest: bool = False
    data_dir: Path = Path("data")


@dataclass(frozen=True)
class FetchResult:
    match_count: int
    act: str
    null_performance_scores: int | None
    kill_count: int
    digest_paths: tuple[Path, ...]
    cache_hits: int = 0
    new_cache_files: int = 0
    pages_requested: int = 0
    validation_warnings: tuple[str, ...] = ()
    manifest_path: Path | None = None


@dataclass(frozen=True)
class FetchProgress:
    pages_requested: int
    matches_seen: int
    cache_hits: int
    new_cache_files: int


def _unwrap(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise APIError("Cached HenrikDev response must be a JSON object.")
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
            # Raw match responses contain every participant. A shared cache can
            # therefore contain matches fetched for other Riot IDs; they are not
            # candidates for this player's history.
            continue
        selected.append(match)
    return selected


def _export_selected_matches(
    options: FetchOptions,
    matches: list[dict[str, Any]],
    act: str,
    *,
    cache_hits: int = 0,
    new_cache_files: int = 0,
    pages_requested: int = 0,
    cached_match_ids: set[str] | None = None,
    cache_entries_total: int = 0,
) -> FetchResult:
    cache_matches_for_query = len(matches)
    matches.sort(key=lambda match: (match.get("metadata", {}).get("started_at") or ""), reverse=True)
    matches = matches[:options.limit]
    wants_flattened_data = options.rounds or options.export_csv or options.export_jsonl or options.export_digest
    rows: list[dict[str, Any]] = []
    kill_rows: list[dict[str, Any]] = []
    if wants_flattened_data:
        for match in matches:
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
            options.data_dir, rows, f"{options.name}#{options.tag}", options.region, act, modes or None
        )
    validation_warnings = tuple(
        f"{row.get('match_id')}: {row['round_validation_warning']}"
        for row in rows if row.get("round_validation_warning")
    )
    selected_ids = [str(match.get("metadata", {}).get("match_id")) for match in matches]
    selected_cache_hits = (
        sum(match_id in cached_match_ids for match_id in selected_ids)
        if cached_match_ids is not None else min(cache_hits, len(matches))
    )
    manifest_path = None
    if options.export_manifest:
        manifest_path = write_manifest(options.data_dir, {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "player": f"{options.name}#{options.tag}",
            "region": options.region,
            "act": act,
            "mode": options.mode or "all",
            "limit": options.limit,
            "selected_match_count": len(matches),
            "selected_match_ids": selected_ids,
            "cache_entries_total": cache_entries_total,
            "cache_hits_selected": selected_cache_hits,
            "new_raw_files": new_cache_files,
            "skipped_cache_entries": max(0, cache_entries_total - cache_matches_for_query),
            "api_pages_requested": pages_requested,
            "validation_warnings": list(validation_warnings),
        })
    return FetchResult(
        match_count=len(matches),
        act=act,
        null_performance_scores=(
            sum(row.get("perf_score") is None for row in rows) if wants_flattened_data else None
        ),
        kill_count=len(kill_rows),
        digest_paths=tuple(digest_paths),
        cache_hits=selected_cache_hits,
        new_cache_files=new_cache_files,
        pages_requested=pages_requested,
        validation_warnings=validation_warnings,
        manifest_path=manifest_path,
    )


def run_export_from_cache(options: FetchOptions) -> FetchResult:
    """Regenerate selected exports from raw cache without constructing an API client."""
    if options.limit <= 0:
        raise APIError("--limit must be a positive integer.")
    raw_dir = options.data_dir / "raw"
    cache = _load_cache(raw_dir)
    if options.act.casefold() == "current":
        player_matches = [
            match for match in cache.values()
            if any(
                str(player.get("name", "")).casefold() == options.name.casefold()
                and str(player.get("tag", "")).casefold() == options.tag.casefold()
                for player in (match.get("players") or []) if isinstance(player, dict)
            )
        ]
        if not player_matches:
            raise APIError(f"No cached matches found for {options.name}#{options.tag}.")
        latest = max(player_matches, key=lambda m: (m.get("metadata", {}).get("started_at") or ""))
        latest_metadata = latest.get("metadata") or {}
        act = str((latest_metadata.get("season") or {}).get("short") or "")
        if not act:
            raise APIError("Newest cached match is missing metadata.season.short; cannot determine the current act.")
    else:
        act = options.act
    matches = _selected_matches(cache, options.name, options.tag, act, options.mode)
    if not matches:
        suffix = f" and mode {options.mode}" if options.mode else ""
        raise APIError(f"No cached matches found for act {act}{suffix}.")
    return _export_selected_matches(
        options, matches, act, cache_hits=len(matches), cached_match_ids=set(cache),
        cache_entries_total=len(cache),
    )


def _write_raw_cache(raw_dir: Path, match_id: str, match: dict[str, Any]) -> None:
    """Write one cache entry atomically so interruption cannot corrupt JSON."""
    payload = json.dumps(match, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=raw_dir,
            prefix=f".{match_id}.", suffix=".tmp", delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, raw_dir / f"{match_id}.json")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def run_fetch(
    options: FetchOptions,
    api_key: str | None = None,
    progress_callback: Callable[[FetchProgress], None] | None = None,
    cancellation_event: threading.Event | None = None,
) -> FetchResult:
    """Execute the workflow and return structured results without CLI output."""
    if options.limit <= 0:
        raise APIError("--limit must be a positive integer.")

    raw_dir = options.data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache = _load_cache(raw_dir)
    initially_cached_ids = set(cache)
    client = HenrikDevClient(api_key=api_key)
    pages_requested = 0
    new_cache_files = 0
    matches_seen = 0
    cached_ids_seen: set[str] = set()
    target_act: str | None = None if options.act.casefold() == "current" else options.act
    prefetched_response: dict[str, Any] | None = None

    if target_act is None:
        latest = client.get_matches_page(options.name, options.tag, options.region, None, 0, 1)
        pages_requested += 1
        latest_matches = latest.get("data")
        if not isinstance(latest_matches, list):
            raise APIError("HenrikDev response has no match list at `data`.")
        if not latest_matches or not isinstance(latest_matches[0], dict):
            raise APIError("HenrikDev returned no matches; there is no current act to select.")
        if options.mode is not None and progress_callback:
            progress_callback(FetchProgress(pages_requested, 0, 0, 0))
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
        if cancellation_event is not None and cancellation_event.is_set():
            raise FetchCancelled("Fetch cancelled. Matches already cached remain available for offline export.")
        if prefetched_response is not None:
            response = prefetched_response
            prefetched_response = None
        else:
            request_size = min(PAGE_SIZE, options.limit)
            response = client.get_matches_page(
                options.name, options.tag, options.region, options.mode, offset, request_size
            )
            pages_requested += 1
        matches = response.get("data")
        if not isinstance(matches, list):
            raise APIError("HenrikDev response has no match list at `data`.")
        matches_seen += len(matches)
        if not matches:
            if progress_callback:
                progress_callback(FetchProgress(pages_requested, matches_seen, len(cached_ids_seen), new_cache_files))
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
                cached_ids_seen.add(match_id)
            if match_id not in cache:
                _write_raw_cache(raw_dir, match_id, match)
                cache[match_id] = match
                new_cache_files += 1

        if progress_callback:
            progress_callback(FetchProgress(pages_requested, matches_seen, len(cached_ids_seen), new_cache_files))

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
    cache_hits = sum(str(match.get("metadata", {}).get("match_id")) in initially_cached_ids for match in act_matches)
    return _export_selected_matches(
        options, act_matches, target_act, cache_hits=cache_hits,
        new_cache_files=new_cache_files, pages_requested=pages_requested,
        cached_match_ids=initially_cached_ids, cache_entries_total=len(cache),
    )
