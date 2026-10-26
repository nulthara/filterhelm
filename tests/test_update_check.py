from __future__ import annotations

import ipaddress

import httpx
import pytest

from app import update_check
from app.pihole_client import _PinnedTransport
from app.url_policy import ValidatedDestination


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    update_check.reset_cache()
    monkeypatch.delenv('FILTERHELM_UPDATE_CHECK', raising=False)
    monkeypatch.setattr(update_check, '__version__', '1.0.2')
    yield
    update_check.reset_cache()


@pytest.mark.parametrize('value', ['off', 'OFF', '0', 'false', 'no', ' off '])
def test_disabled_values_skip_fetch(monkeypatch, value):
    monkeypatch.setenv('FILTERHELM_UPDATE_CHECK', value)
    monkeypatch.setattr(update_check, 'fetch_latest_release', lambda: pytest.fail('unexpected fetch'))
    assert update_check.check_for_update() == update_check.UpdateStatus(False, '1.0.2', None, False)


@pytest.mark.parametrize('value', [None, 'on'])
def test_unset_or_other_value_is_enabled(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv('FILTERHELM_UPDATE_CHECK', value)
    monkeypatch.setattr(update_check, 'fetch_latest_release', lambda: '1.1.0')
    assert update_check.check_for_update() == update_check.UpdateStatus(True, '1.0.2', '1.1.0', True)


def test_newer_release_flags_update(monkeypatch):
    monkeypatch.setattr(update_check, 'fetch_latest_release', lambda: '1.1.0')
    assert update_check.check_for_update().update_available is True


@pytest.mark.parametrize('latest', ['1.0.2', '0.9.9'])
def test_same_or_older_release_does_not_flag(monkeypatch, latest):
    monkeypatch.setattr(update_check, 'fetch_latest_release', lambda: latest)
    assert update_check.check_for_update().update_available is False


def test_unparseable_current_version_never_flags(monkeypatch):
    monkeypatch.setattr(update_check, '__version__', '1.0.2.dev1')
    monkeypatch.setattr(update_check, 'fetch_latest_release', lambda: '9.0.0')
    assert update_check.check_for_update().update_available is False


def test_success_is_cached_for_six_hours(monkeypatch):
    calls = []
    def fetch():
        calls.append(1)
        return '1.1.0'
    monkeypatch.setattr(update_check, 'fetch_latest_release', fetch)
    for now in [0, 21599]:
        assert update_check.check_for_update(clock=lambda: now).latest == '1.1.0'
    assert len(calls) == 1
    update_check.check_for_update(clock=lambda: 21600)
    assert len(calls) == 2


@pytest.mark.parametrize('raises', [True, False])
def test_failure_is_cached_for_fifteen_minutes(monkeypatch, caplog, raises):
    calls = []
    def fetch():
        calls.append(1)
        if raises:
            raise httpx.ConnectError('secret response detail')
        return None
    monkeypatch.setattr(update_check, 'fetch_latest_release', fetch)
    for now in [0, 899]:
        assert update_check.check_for_update(clock=lambda: now).latest is None
    assert len(calls) == 1
    update_check.check_for_update(clock=lambda: 900)
    assert len(calls) == 2
    if raises:
        assert 'ConnectError' in caplog.text
    assert 'secret response detail' not in caplog.text


def test_fetch_parses_release_and_sends_headers(monkeypatch):
    def handle(request):
        assert request.url.path == '/repos/nulthara/filterhelm/releases/latest'
        assert request.headers['Accept'] == 'application/vnd.github+json'
        assert request.headers['User-Agent'].startswith('FilterHelm/')
        return httpx.Response(200, json={'tag_name': 'v1.1.0', 'draft': False, 'prerelease': False})
    monkeypatch.setattr(update_check, '_open_client', lambda: httpx.Client(
        base_url='https://api.github.com', transport=httpx.MockTransport(handle),
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'FilterHelm/1.0.2'}))
    assert update_check.fetch_latest_release() == '1.1.0'


@pytest.mark.parametrize('status,body', [
    (404, {}), (302, {}), (200, []), (200, {}),
    (200, {'tag_name': 'nightly'}), (200, {'tag_name': '1.1.0', 'prerelease': True}),
    (200, {'tag_name': '1.1.0', 'draft': True}),
])
def test_fetch_rejects_bad_payloads(monkeypatch, status, body):
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(status, json=body, headers={'Location': '/redirected'})
    monkeypatch.setattr(update_check, '_open_client', lambda: httpx.Client(
        base_url='https://api.github.com', transport=httpx.MockTransport(handle)))
    assert update_check.fetch_latest_release() is None
    assert calls == ['/repos/nulthara/filterhelm/releases/latest']


def test_open_client_uses_pinned_policy_transport(monkeypatch):
    destination = ValidatedDestination('https://api.github.com', 'api.github.com', None,
                                       (ipaddress.ip_address('8.8.8.8'),))
    monkeypatch.setattr(update_check, 'resolve_update_check_destination', lambda: destination)
    with update_check._open_client() as client:
        assert isinstance(client._transport, _PinnedTransport)
        assert client._transport.destination == destination
        assert client.follow_redirects is False
        assert client._trust_env is False
        assert client.timeout.connect == 5.0
        assert client.headers['Accept'] == 'application/vnd.github+json'
        assert client.headers['User-Agent'] == 'FilterHelm/1.0.2'


@pytest.mark.parametrize('value,expected', [('v1.2.3', (1, 2, 3)), ('1.2.3', (1, 2, 3)),
                                         ('1.2.3.dev1', None), ('1.2.3\n', None), (None, None)])
def test_parse_version(value, expected):
    assert update_check.parse_version(value) == expected
