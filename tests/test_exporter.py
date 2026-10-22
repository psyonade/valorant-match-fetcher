import csv
import json

import pytest

import exporter
from exporter import write_digest, write_match_exports


def _row(match_id, result):
    return {
        "schema_version": "1.0",
        "match_id": match_id,
        "started_at": f"2026-10-{match_id:02d}T00:00:00Z",
        "result": result,
        "won": result == "win",
        "rounds_won": 15,
        "rounds_lost": 15 if result == "draw" else 12,
        "round_records_total": 30 if result == "draw" else 27,
        "rounds_with_valid_result": 30 if result == "draw" else 27,
        "round_validation_warning": None,
        "first_bloods": 1,
        "first_deaths": 1,
        "perf_rank_in_match": None,
        "perf_score": 250.0,
        "agent": "Sage",
        "map": "Haven",
        "queue": "competitive",
    }


def test_digest_uses_normalized_results_and_excludes_unknown_from_rates(tmp_path):
    rows = [_row(1, "win"), _row(2, "loss"), _row(3, "draw"), _row(4, "unknown")]

    path = write_digest(tmp_path, rows, "Example#TAG", "ap", "e11a6", ["competitive"])[0]
    text = path.read_text(encoding="utf-8")

    assert "win_rate_classified_%" in text
    assert "win_rate_decisive_%" in text
    assert "| 4 | 1 | 1 | 1 | 1 | 33.3 | 50.0 |" in text
    assert "| Result | K | D | A |" in text
    assert "| 2026-10-03T00:00:00Z | Haven | competitive | Sage | draw |" in text


def test_digest_lists_round_validation_warnings(tmp_path):
    row = _row(1, "win")
    row["round_validation_warning"] = "round records with a recognized winner (26) do not match team round totals (27)"

    path = write_digest(tmp_path, [row], "Example#TAG", "ap", "e11a6", None)[0]
    text = path.read_text(encoding="utf-8")

    assert "### Round-data validation" in text
    assert row["round_validation_warning"] in text


def test_csv_jsonl_share_normalized_rows_and_newest_first_order(tmp_path):
    rows = [_row(1, "win"), _row(2, "loss")]

    write_match_exports(tmp_path, rows, [], include_csv=True, include_jsonl=True)

    csv_rows = list(csv.DictReader((tmp_path / "matches.csv").open(encoding="utf-8", newline="")))
    jsonl_rows = [json.loads(line) for line in (tmp_path / "matches.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["match_id"] for row in csv_rows] == ["2", "1"]
    assert [row["match_id"] for row in jsonl_rows] == [2, 1]
    for csv_row, jsonl_row in zip(csv_rows, jsonl_rows):
        assert csv_row["schema_version"] == jsonl_row["schema_version"] == "1.0"
        assert csv_row["result"] == jsonl_row["result"]


def test_export_replace_failure_keeps_existing_file_and_removes_temp(tmp_path, monkeypatch):
    destination = tmp_path / "matches.csv"
    destination.write_text("old contents\n", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(exporter.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        write_match_exports(tmp_path, [_row(1, "win")], [], include_csv=True)

    assert destination.read_text(encoding="utf-8") == "old contents\n"
    assert list(tmp_path.glob("*.tmp")) == []
