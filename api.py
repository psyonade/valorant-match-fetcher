"""HenrikDev Valorant API client."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import requests


BASE_URL = "https://api.henrikdev.xyz"
MAX_ATTEMPTS = 5


class APIError(RuntimeError):
    """An API response that the CLI can explain to the user."""


class HenrikDevClient:
    def __init__(self, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("HENRIK_API_KEY")
        if not self.api_key:
            raise APIError("HENRIK_API_KEY is not set. Set it in the environment and retry.")
        self.timeout = timeout
        self.last_rate_headers: dict[str, str] = {}
        self.session = requests.Session()
        self.session.headers.update({"Authorization": self.api_key, "Accept": "application/json"})

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        value = headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    until = parsedate_to_datetime(value)
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                    return max(0.0, (until - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        relative_reset = headers.get("RateLimit-Reset")
        if relative_reset:
            try:
                return max(0.0, float(relative_reset))
            except ValueError:
                pass
        reset = headers.get("X-RateLimit-Reset") or headers.get("X-Rate-Limit-Reset")
        if reset:
            try:
                stamp = float(reset)
                if stamp > 10_000_000_000:  # milliseconds
                    stamp /= 1000
                return max(0.0, stamp - time.time()) if stamp > 1_000_000_000 else max(0.0, stamp)
            except ValueError:
                pass
        return None

    def _remember_rate_headers(self, headers: Mapping[str, str]) -> None:
        limit = headers.get("X-RateLimit-Limit") or headers.get("X-Rate-Limit-Limit") or headers.get("RateLimit-Limit")
        remaining = (
            headers.get("X-RateLimit-Remaining")
            or headers.get("X-Rate-Limit-Remaining")
            or headers.get("RateLimit-Remaining")
        )
        reset = headers.get("X-RateLimit-Reset") or headers.get("X-Rate-Limit-Reset") or headers.get("RateLimit-Reset")
        self.last_rate_headers = {
            key: value for key, value in (("limit", limit), ("remaining", remaining), ("reset", reset))
            if value is not None
        }

    def _respect_rate_headers(self, headers: Mapping[str, str]) -> None:
        self._remember_rate_headers(headers)
        remaining = self.last_rate_headers.get("remaining")
        if remaining is None:
            return
        try:
            if int(remaining) > 0:
                return
        except ValueError:
            return
        delay = HenrikDevClient._retry_after(headers)
        if delay:
            time.sleep(min(delay, 300.0))

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise APIError(f"Could not reach HenrikDev API after {MAX_ATTEMPTS} attempts: {exc}") from exc
                time.sleep(min(2**attempt, 30))
                continue

            self._remember_rate_headers(response.headers)
            status = response.status_code
            if status in (401, 403, 404):
                messages = {
                    401: "HenrikDev rejected the API key (401). Check HENRIK_API_KEY.",
                    403: "HenrikDev denied this request (403). Check API key access or endpoint availability.",
                    404: "HenrikDev could not find this Riot ID or resource (404). Check name, tag, and region.",
                }
                raise APIError(messages[status])
            if status == 429 or status >= 500:
                if attempt + 1 == MAX_ATTEMPTS:
                    message = "rate limit (429)" if status == 429 else f"server error ({status})"
                    raise APIError(f"HenrikDev returned {message} after {MAX_ATTEMPTS} attempts.")
                delay = self._retry_after(response.headers)
                time.sleep(min(delay if delay is not None else 2**attempt, 300.0))
                continue
            self._respect_rate_headers(response.headers)
            if not response.ok:
                raise APIError(f"HenrikDev returned HTTP {status}: {response.reason or 'request failed'}.")
            try:
                body = response.json()
            except requests.JSONDecodeError as exc:
                raise APIError("HenrikDev returned invalid JSON.") from exc
            if not isinstance(body, dict):
                raise APIError("HenrikDev returned an unexpected JSON response (expected an object).")
            return body
        raise APIError("HenrikDev request failed after retries.")

    def get_matches_page(
        self, name: str, tag: str, region: str, mode: str | None, start: int, size: int = 1
    ) -> dict[str, Any]:
        path = "/valorant/v4/matches/{}/{}/{}/{}".format(
            quote(region, safe=""), "pc", quote(name, safe=""), quote(tag, safe="")
        )
        params: dict[str, Any] = {"size": size, "start": start}
        if mode:
            params["mode"] = mode
        return self._get(path, params)

    def iter_match_responses(
        self,
        name: str,
        tag: str,
        region: str = "ap",
        mode: str | None = None,
        start: int = 0,
        size: int = 1,
        max_matches: int | None = None,
    ) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        """Yield (one-match response envelope, match) from newest to oldest."""
        offset = start
        yielded = 0
        while max_matches is None or yielded < max_matches:
            count = min(size, max_matches - yielded) if max_matches is not None else size
            body = self.get_matches_page(name, tag, region, mode, offset, count)
            data = body.get("data")
            if not isinstance(data, list):
                raise APIError("HenrikDev response has no match list at `data`.")
            if not data:
                return
            for match in data:
                if not isinstance(match, dict):
                    raise APIError("HenrikDev returned a malformed match entry.")
                single = dict(body)
                single["data"] = [match]
                yield single, match
                yielded += 1
                if max_matches is not None and yielded >= max_matches:
                    return
            offset += len(data)

