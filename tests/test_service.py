import copy
import json
from pathlib import Path

import pytest

import service
from api import APIError


FIXTURE = Path(__file__).parent / "fixtures" / "sample_match.json"


def sample_match():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_selected_matches_ignores_unrelated_players_in_shared_cache():
    target = sample_match()
    unrelated = copy.deepcopy(target)
    unrelated["metadata"]["match_id"] = "unrelated-match"
    unrelated["players"] = [p for p in unrelated["players"] if p["name"] != "TestPlayer"]

    selected = service._selected_matches(
        {"target": target, "unrelated-match": unrelated}, "testplayer", "test", "e11a6", None
    )

    assert selected == [target]


def test_shared_match_can_be_selected_for_both_players():
    match = sample_match()

    first = service._selected_matches({"shared": match}, "TestPlayer", "TEST", "e11a6", None)
    second = service._selected_matches({"shared": match}, "Player1", "TAG1", "e11a6", None)

    assert first == [match]
    assert second == [match]


def test_write_raw_cache_creates_parseable_json(tmp_path):
    match = sample_match()

    service._write_raw_cache(tmp_path, "fixture-match", match)

    saved = json.loads((tmp_path / "fixture-match.json").read_text(encoding="utf-8"))
    assert saved == match
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_atomic_replace_preserves_old_cache_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "fixture-match.json"
    destination.write_text('{"old": true}\n', encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(service.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        service._write_raw_cache(tmp_path, "fixture-match", sample_match())

    assert destination.read_text(encoding="utf-8") == '{"old": true}\n'
    assert list(tmp_path.glob("*.tmp")) == []


class FakeClient:
    pages = []
    calls = []

    def __init__(self, api_key=None):
        self.responses = list(type(self).pages)
        type(self).calls = []

    def get_matches_page(self, name, tag, region, mode, start, size):
        type(self).calls.append((name, tag, region, mode, start, size))
        if not self.responses:
            raise AssertionError("Unexpected additional page request")
        return self.responses.pop(0)


def _identified_match(match_id, started_at):
    match = sample_match()
    match["metadata"]["match_id"] = match_id
    match["metadata"]["started_at"] = started_at
    return match


def _options(data_dir, limit=10):
    return service.FetchOptions(
        name="TestPlayer", tag="TEST", act="e11a6", limit=limit, data_dir=data_dir
    )


def test_fetch_skips_other_players_cache_and_fetches_target_match(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    unrelated = _identified_match("other-player-match", "2026-10-20T00:00:00Z")
    unrelated["players"] = [player for player in unrelated["players"] if player["name"] != "TestPlayer"]
    (raw_dir / "other-player-match.json").write_text(json.dumps(unrelated), encoding="utf-8")
    target = _identified_match("target-match", "2026-10-21T00:00:00Z")
    FakeClient.pages = [{"data": [target]}]
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)

    result = service.run_fetch(_options(tmp_path, limit=1), api_key="fake")

    assert result.match_count == 1
    assert result.cache_hits == 0
    assert result.new_cache_files == 1
    assert result.pages_requested == 1
    assert (raw_dir / "target-match.json").exists()
    assert len(FakeClient.calls) == 1


def test_fetch_rejects_api_match_missing_requested_player(tmp_path, monkeypatch):
    invalid = _identified_match("invalid-match", "2026-10-21T00:00:00Z")
    invalid["players"] = [player for player in invalid["players"] if player["name"] != "TestPlayer"]
    FakeClient.pages = [{"data": [invalid]}]
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)

    with pytest.raises(APIError, match="returned without player"):
        service.run_fetch(_options(tmp_path), api_key="fake")


def test_fetch_stops_at_cached_boundary_and_deduplicates_match_ids(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    old = _identified_match("old-match", "2026-10-19T00:00:00Z")
    old_path = raw_dir / "old-match.json"
    old_bytes = (json.dumps(old, indent=2) + "\n").encode("utf-8")
    old_path.write_bytes(old_bytes)
    newest = _identified_match("new-match", "2026-10-21T00:00:00Z")
    duplicate = _identified_match("new-match", "2026-10-22T00:00:00Z")
    FakeClient.pages = [{"data": [newest, old, duplicate]}]
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)
    progress = []

    result = service.run_fetch(_options(tmp_path, limit=2), api_key="fake", progress_callback=progress.append)

    assert result.match_count == 2
    assert result.cache_hits == 1
    assert result.new_cache_files == 1
    assert result.pages_requested == 1
    assert progress[-1] == service.FetchProgress(1, 3, 1, 1)
    assert len(FakeClient.calls) == 1
    assert old_path.read_bytes() == old_bytes
    assert json.loads((raw_dir / "new-match.json").read_text(encoding="utf-8"))["metadata"]["started_at"] == "2026-10-21T00:00:00Z"


def test_fetch_orders_out_of_order_pages_and_respects_limit(tmp_path, monkeypatch):
    matches = [
        _identified_match("page1-new", "2026-10-22T00:00:00Z"),
        _identified_match("page1-old", "2026-10-20T00:00:00Z"),
        _identified_match("page2-middle", "2026-10-21T00:00:00Z"),
        _identified_match("page2-oldest", "2026-10-19T00:00:00Z"),
    ]
    FakeClient.pages = [{"data": matches[:2]}, {"data": matches[2:]}]
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)
    options = _options(tmp_path, limit=3)
    options = service.FetchOptions(**{**options.__dict__, "export_jsonl": True})

    result = service.run_fetch(options, api_key="fake")

    rows = [json.loads(line) for line in (tmp_path / "matches.jsonl").read_text(encoding="utf-8").splitlines()]
    assert result.match_count == 3
    assert [row["match_id"] for row in rows] == ["page1-new", "page2-middle", "page1-old"]
    assert len(FakeClient.calls) == 2


def test_repeated_fetch_is_deterministic_and_does_not_rewrite_cached_files(tmp_path, monkeypatch):
    newest = _identified_match("newest", "2026-10-22T00:00:00Z")
    older = _identified_match("older", "2026-10-21T00:00:00Z")
    page = {"data": [newest, older]}
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)
    options = service.FetchOptions(
        name="TestPlayer", tag="TEST", act="e11a6", limit=2,
        export_jsonl=True, data_dir=tmp_path,
    )
    FakeClient.pages = [page]
    first = service.run_fetch(options, api_key="fake")
    cache_paths = [tmp_path / "raw" / "newest.json", tmp_path / "raw" / "older.json"]
    before = [path.read_bytes() for path in cache_paths]
    first_export = (tmp_path / "matches.jsonl").read_bytes()

    FakeClient.pages = [page]
    second = service.run_fetch(options, api_key="fake")

    assert first.match_count == second.match_count == 2
    assert second.new_cache_files == 0
    assert second.cache_hits == 2
    assert [path.read_bytes() for path in cache_paths] == before
    assert (tmp_path / "matches.jsonl").read_bytes() == first_export


def test_fetch_can_use_cache_when_api_page_ends_early(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    cached = _identified_match("cached-match", "2026-10-20T00:00:00Z")
    (raw_dir / "cached-match.json").write_text(json.dumps(cached), encoding="utf-8")
    FakeClient.pages = [{"data": []}]
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)

    result = service.run_fetch(_options(tmp_path), api_key="fake")

    assert result.match_count == 1
    assert result.act == "e11a6"
    assert result.cache_hits == 1
    assert result.pages_requested == 1


def test_offline_export_regenerates_selected_outputs_without_api_client(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    cached = _identified_match("cached-match", "2026-10-20T00:00:00Z")
    (raw_dir / "cached-match.json").write_text(json.dumps(cached), encoding="utf-8")

    def unexpected_api_client(*_args, **_kwargs):
        raise AssertionError("Offline export must not create an API client")

    monkeypatch.setattr(service, "HenrikDevClient", unexpected_api_client)
    options = service.FetchOptions(
        name="TestPlayer", tag="TEST", act="current", limit=10, export_csv=True,
        export_jsonl=True, export_manifest=True, data_dir=tmp_path,
    )

    result = service.run_export_from_cache(options)

    assert result.match_count == 1
    assert result.act == "e11a6"
    assert result.cache_hits == 1
    assert result.new_cache_files == 0
    assert result.pages_requested == 0
    assert (tmp_path / "matches.csv").exists()
    exported = json.loads((tmp_path / "matches.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert exported["match_id"] == "cached-match"
    manifest = json.loads((tmp_path / "fetch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.0"
    assert manifest["selected_match_ids"] == ["cached-match"]
    assert manifest["cache_hits_selected"] == 1
    assert manifest["api_pages_requested"] == 0


def test_fetch_returns_normalized_round_warnings_for_ui_and_cli(tmp_path, monkeypatch):
    match = _identified_match("warning-match", "2026-10-20T00:00:00Z")
    match["rounds"] = []
    FakeClient.pages = [{"data": [match]}]
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)
    options = service.FetchOptions(
        name="TestPlayer", tag="TEST", act="e11a6", limit=1, export_csv=True, data_dir=tmp_path
    )

    result = service.run_fetch(options, api_key="fake")

    assert len(result.validation_warnings) == 1
    assert "warning-match" in result.validation_warnings[0]


def test_cooperative_cancel_stops_between_pages_and_keeps_cached_matches(tmp_path, monkeypatch):
    target = _identified_match("saved-before-cancel", "2026-10-20T00:00:00Z")
    FakeClient.pages = [{"data": [target]}]
    monkeypatch.setattr(service, "HenrikDevClient", FakeClient)
    cancellation = service.threading.Event()
    options = _options(tmp_path, limit=2)

    with pytest.raises(service.FetchCancelled, match="already cached"):
        service.run_fetch(
            options,
            api_key="fake",
            cancellation_event=cancellation,
            progress_callback=lambda _progress: cancellation.set(),
        )

    assert (tmp_path / "raw" / "saved-before-cancel.json").exists()
    assert len(FakeClient.calls) == 1


def test_malformed_cache_json_reports_file_path(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    corrupt = raw_dir / "broken-match.json"
    corrupt.write_text("not json", encoding="utf-8")

    with pytest.raises(APIError, match="broken-match.json"):
        service._load_cache(raw_dir)


def test_valid_json_with_wrong_cache_shape_reports_file_path(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    malformed = raw_dir / "broken-match.json"
    malformed.write_text("[]", encoding="utf-8")

    with pytest.raises(APIError, match="Cached HenrikDev response must be a JSON object"):
        service._load_cache(raw_dir)
