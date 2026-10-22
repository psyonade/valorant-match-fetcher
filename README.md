# Valorant match data fetcher

Fetches HenrikDev v4 match history for a Riot ID, caches each raw match by ID, and creates compact exports you can share with an LLM.

## Setup

Requires Python 3.11+ and `requests`. From a clean checkout, create the virtual environment and install the pinned development dependencies (runtime, tests, and build tool):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the offline test suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Desktop app

To build the Windows one-file app, run this from PowerShell in the project folder:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build-exe.ps1
```

The script installs the pinned PyInstaller build dependency into `.venv` and creates
`dist\ValorantMatchFetcher.exe`. Start the app, enter your Riot ID and HenrikDev API
key, choose any optional exports, and click **Fetch matches**. The key is masked in
the window and used only for that fetch. Raw JSON and selected exports go to
`%LOCALAPPDATA%\ValorantMatchFetcher\data` by default; use **Browse** to choose
another folder. **Open output folder** opens the selected location.

The executable is built for Windows and should be rebuilt after changing the Python
source. Building requires internet access once to download PyInstaller if it is not already
cached. CI runs the test suite and the same build script from a clean Python 3.11 setup on pushes
and pull requests; its executable is uploaded as a workflow artifact.

## One-command PowerShell fetch

From this folder, enter one command. It securely prompts for the HenrikDev API key, runs the fetch, then clears the key from the child process environment. The key is not written to disk or included in command history:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run-fetch.ps1 -Name "PlayerName" -Tag "1234" -Region ap -Mode competitive -Limit 100
```

Options:

- `-Name` and `-Tag`: Riot ID parts.
- `-Region`: HenrikDev affinity; defaults to `ap`.
- `-Act`: act code or `current`; defaults to `current`.
- `-Mode`: exact queue ID such as `competitive`; omit for all modes.
- `-Limit`: maximum matches; defaults to 100.
- `-Csv`: also write `matches.csv`.
- `-Jsonl`: also write `matches.jsonl`.
- `-Digest`: also write the compact LLM Markdown digest.
- `-Manifest`: also write `fetch_manifest.json` with query, selected IDs, cache counts, and validation warnings.
- `-Rounds`: also write `kills.csv` (raw cached match JSON already includes round and kill data).
- `--manifest` is the corresponding switch for direct Python CLI use.
- `--from-cache` (Python CLI only): regenerate selected exports from the existing raw cache without an API key or network request. Combine with one or more export flags, for example `--from-cache --csv --digest`.

You can also call Python directly after setting `HENRIK_API_KEY` in the current PowerShell process:

```powershell
.\.venv\Scripts\python.exe fetch.py --name "PlayerName" --tag "1234" --region ap --mode competitive --limit 100 --digest --rounds
```

To regenerate processed files after changing the parser, without fetching or spending API quota:

```powershell
.\.venv\Scripts\python.exe fetch.py --name "PlayerName" --tag "1234" --from-cache --csv --jsonl --digest --rounds
```

The desktop app also has **Regenerate exports from cache**. Select the same player/filter fields and desired exports; it does not require the key field.

## Output

- `data/raw/<match_id>.json`: complete raw HenrikDev match object, cached once per match ID.
- `data/matches.csv`: optional flattened per-match data, requested with `--csv` / `-Csv`.
- `data/matches.jsonl`: optional flattened per-match data, requested with `--jsonl` / `-Jsonl`.
- `data/kills.csv`: optional one-row-per-kill export, requested with `--rounds` / `-Rounds`.
- `data/llm_digest.md`, or `data/llm_digest_part*.md` for large exports: optional compact aggregates and per-match table, requested with `--digest` / `-Digest`.
- `data/fetch_manifest.json`: optional fetch provenance and validation summary, requested with `--manifest`.

By default the fetcher only caches raw match JSON. Optional exports are created or updated only when their switch is specified; existing export files are left untouched when omitted. For LLM analysis, upload the raw JSON files. The digest is a compact alternative, and `kills.csv` duplicates event data already in the raw matches.

### Result and round fields in processed exports

- `won` preserves HenrikDev's team Boolean. A `false` value alone does not distinguish a loss from a draw.
- `result` is normalized to `win`, `loss`, `draw`, or `unknown`. When both team round totals are available, the parser compares them; equal totals mean `draw`. Otherwise, an unambiguous `true` team Boolean can establish a win or loss. Ambiguous Boolean values remain `unknown`.
- `round_records_total` counts entries in the raw rounds array. `rounds_with_valid_result` counts records whose `winning_team` is `Blue` or `Red`. Missing or malformed arrays remain `null`; a valid empty array is `0`.
- `round_validation_warning` is set when the recognized round-result count differs from the player's team round totals. Side split counts still require usable round role metadata.
- Digest win rates exclude `unknown` results. `win_rate_classified_%` uses wins + losses + draws as its denominator; `win_rate_decisive_%` uses wins + losses and excludes draws.

### Metric provenance

- `first_bloods` counts rounds whose `rounds[].first_blood` player is the player being flattened. It does not fall back to ordering the kill events.
- `first_deaths` uses the earliest timestamped player-death event in each round. It credits that death only when the earliest event is a kill by a distinct player on the opposing team. A known self/same-team/environmental event at round start is not credited as an opposing first death. If any player-death event lacks a timestamp or the earliest timestamp is tied between victims, the match's `first_deaths` is `null` because ordering is incomplete or ambiguous.
- `multikills_2k` through `multikills_5k` count rounds with exactly that many distinct opposing-team player victims credited to the player. Exact duplicate event rows do not add victims. Player-vs-environment, same-team, and self-kill events are excluded.
- `kd` is kills divided by deaths from `players[].stats`; it is `null` when deaths are zero or either value is missing. `adr` is HenrikDev's `players[].performance.breakdown.damage` value, not a local damage-per-round calculation. `hs_pct` is headshots divided by headshots + bodyshots + legshots, times 100; it is a hit-location share, not accuracy.
- The event rules are conservative interpretations of HenrikDev's event list. The raw JSON remains the source of record; provider event completeness and semantics are not independently guaranteed.

### Processed export contract

`matches.csv` and `matches.jsonl` contain the same normalized match rows, ordered newest first. Each row has `schema_version: "1.0"`; additive field changes should preserve existing meanings, and a breaking change should increment this version. `kills.csv` is one row per kill event, not a complete round table. The digest states its schema version and source and uses the same normalized match rows for its per-match table and aggregates. Each output file is replaced atomically after its full contents are ready; a multi-file export is not a single all-or-nothing transaction.

## Verified fields and limitations

- Match history uses `GET /valorant/v4/matches/{affinity}/{platform}/{name}/{tag}` with optional `mode`, `size`, and `start` ([HenrikDev endpoint docs](https://docs.henrikdev.xyz/api-reference/valorant/get-matches-by-name-v4)).
- Match-level Performance Score is `players[].performance.score`. Riot says Performance Score is 0–500 and replaced ACS in patch 13.06; its formula and historical comparability are limited ([patch notes](https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-13-06/)).
- The live sample did not expose RR changes, clutches, or a per-kill headshot flag; those values remain null. `stats.score` is not Performance Score.
- Team/side statistics depend on role and round metadata being present. Missing values remain null rather than being inferred.
- HenrikDev is unofficial; available history and fields can change. Rate limits are read from response headers when provided, with capped retries for 429/5xx responses.
- Raw match responses include other players' Riot IDs/PUUIDs and round/kill details. Review the files before sharing them with an LLM.
