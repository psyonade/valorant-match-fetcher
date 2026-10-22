import requests
import pytest

import api
from api import APIError, HenrikDevClient


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, reason="failure"):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        self.reason = reason
        self.ok = 200 <= status_code < 400

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.headers = {}
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client_with(outcomes):
    client = HenrikDevClient(api_key="test-key", timeout=4)
    headers = dict(client.session.headers)
    client.session = FakeSession(outcomes)
    client.session.headers.update(headers)
    return client


def test_match_request_quotes_riot_id_and_passes_queue_paging_parameters():
    client = client_with([FakeResponse(body={"data": []})])

    result = client.get_matches_page("Player Name", "A/B", "ap", "competitive", 10, 5)

    assert result == {"data": []}
    url, params, timeout = client.session.calls[0]
    assert url.endswith("/valorant/v4/matches/ap/pc/Player%20Name/A%2FB")
    assert params == {"size": 5, "start": 10, "mode": "competitive"}
    assert timeout == 4
    assert client.session.headers["Authorization"] == "test-key"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_not_found_statuses_fail_without_retry(status):
    client = client_with([FakeResponse(status_code=status)])

    with pytest.raises(APIError, match=f"{status}"):
        client.get_matches_page("Player", "TAG", "ap", None, 0, 1)

    assert len(client.session.calls) == 1


def test_rate_limit_retries_then_succeeds(monkeypatch):
    delays = []
    monkeypatch.setattr(api.time, "sleep", delays.append)
    client = client_with([
        FakeResponse(status_code=429, headers={"Retry-After": "3"}),
        FakeResponse(body={"data": []}, headers={"RateLimit-Limit": "20", "RateLimit-Remaining": "8"}),
    ])

    assert client.get_matches_page("Player", "TAG", "ap", None, 0, 1) == {"data": []}
    assert delays == [3.0]
    assert len(client.session.calls) == 2
    assert client.last_rate_headers == {"limit": "20", "remaining": "8"}


def test_server_errors_stop_after_bounded_attempts(monkeypatch):
    delays = []
    monkeypatch.setattr(api.time, "sleep", delays.append)
    client = client_with([FakeResponse(status_code=503) for _ in range(api.MAX_ATTEMPTS)])

    with pytest.raises(APIError, match=f"after {api.MAX_ATTEMPTS} attempts"):
        client.get_matches_page("Player", "TAG", "ap", None, 0, 1)

    assert len(client.session.calls) == api.MAX_ATTEMPTS
    assert delays == [1, 2, 4, 8]


def test_timeout_is_retried_but_invalid_json_fails_clearly(monkeypatch):
    delays = []
    monkeypatch.setattr(api.time, "sleep", delays.append)
    client = client_with([requests.Timeout("slow"), FakeResponse(body={"data": []})])

    assert client.get_matches_page("Player", "TAG", "ap", None, 0, 1) == {"data": []}
    assert delays == [1]

    invalid = client_with([FakeResponse(body=requests.JSONDecodeError("bad", "x", 0))])
    with pytest.raises(APIError, match="invalid JSON"):
        invalid.get_matches_page("Player", "TAG", "ap", None, 0, 1)


def test_non_object_json_fails_clearly():
    client = client_with([FakeResponse(body=[{"data": []}])])

    with pytest.raises(APIError, match="expected an object"):
        client.get_matches_page("Player", "TAG", "ap", None, 0, 1)
