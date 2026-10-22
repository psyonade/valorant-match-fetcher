# Valorant match data fetcher

Fetches HenrikDev v4 match history for a Riot ID, caches each raw match by ID, and creates compact exports you can share with an LLM.

## Setup

Requires Python 3.11+ and `requests`. The project virtual environment is expected at `.venv`:

```powershell
uv pip install --python .venv\Scripts\python.exe requests
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
source. Building requires internet access once to download PyInstaller if it is not
already cached.

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
- `-Rounds`: also write `kills.csv` (raw cached match JSON already includes round and kill data).

You can also call Python directly after setting `HENRIK_API_KEY` in the current PowerShell process:

```powershell
.\.venv\Scripts\python.exe fetch.py --name "PlayerName" --tag "1234" --region ap --mode competitive --limit 100 --digest --rounds
```

## Output

- `data/raw/<match_id>.json`: complete raw HenrikDev match object, cached once per match ID.
- `data/matches.csv`: optional flattened per-match data, requested with `--csv` / `-Csv`.
- `data/matches.jsonl`: optional flattened per-match data, requested with `--jsonl` / `-Jsonl`.
- `data/kills.csv`: optional one-row-per-kill export, requested with `--rounds` / `-Rounds`.
- `data/llm_digest.md`, or `data/llm_digest_part*.md` for large exports: optional compact aggregates and per-match table, requested with `--digest` / `-Digest`.

By default the fetcher only caches raw match JSON. Optional exports are created or updated only when their switch is specified; existing export files are left untouched when omitted. For LLM analysis, upload the raw JSON files. The digest is a compact alternative, and `kills.csv` duplicates event data already in the raw matches.

## Verified fields and limitations

- Match history uses `GET /valorant/v4/matches/{affinity}/{platform}/{name}/{tag}` with optional `mode`, `size`, and `start` ([HenrikDev endpoint docs](https://docs.henrikdev.xyz/api-reference/valorant/get-matches-by-name-v4)).
- Match-level Performance Score is `players[].performance.score`. Riot says Performance Score is 0–500 and replaced ACS in patch 13.06; its formula and historical comparability are limited ([patch notes](https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-13-06/)).
- The live sample did not expose RR changes, clutches, or a per-kill headshot flag; those values remain null. `stats.score` is not Performance Score.
- Team/side statistics depend on role and round metadata being present. Missing values remain null rather than being inferred.
- HenrikDev is unofficial; available history and fields can change. Rate limits are read from response headers when provided, with capped retries for 429/5xx responses.
- Raw match responses include other players' Riot IDs/PUUIDs and round/kill details. Review the files before sharing them with an LLM.
