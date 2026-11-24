"""Property-based tests (hypothesis): parsers and validators of untrusted text never raise unexpected
exceptions, and keep their invariants for any input."""

from __future__ import annotations

import datetime as dt
import inspect
import string
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import validation
from app.redaction import MAX_LOG_LINE, MAX_MESSAGE, REDACTED, log_line, redact, safe_message
from app.routers.hooks import _repo_key, _signature_ok
from app.services import config_file, schedules
from app.services.alerts import display_host, url_problem
from app.urls import split_url
from app.worker.http_check import check

FAST = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
ANY_TEXT = st.text(max_size=300)
URLISH = st.one_of(
    ANY_TEXT,
    st.builds(
        lambda scheme, host, port, path: f"{scheme}://{host}{port}{path}",
        st.sampled_from(["https", "http", "ftp", "", "HTTPS"]),
        st.one_of(st.from_regex(r"[a-z0-9.\-\[\]:@%]{0,40}", fullmatch=True), ANY_TEXT),
        st.sampled_from(["", ":443", ":0", ":99999", ":-1", ":abc"]),
        st.from_regex(r"[/a-zA-Z0-9?#%.\-]{0,40}", fullmatch=True),
    ),
)
JSONISH = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=20),
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=10), inner, max_size=4),
    max_leaves=20,
)
CURRENT = {
    "port": 8080,
    "cpu": "1",
    "memory": "2Gi",
    "min_instances": 0,
    "max_instances": 3,
    "concurrency": None,
    "exposure": "public",
    "health_check_path": "/healthz",
    "environment": [],
    "secret_refs": [],
}


# ----- cloudbridge.yaml -----


@FAST
@given(ANY_TEXT)
def test_config_file_any_text(text: str) -> None:
    try:
        result = config_file.parse(text, CURRENT)
    except config_file.ConfigFileError as error:
        assert error.errors
        return
    assert set(CURRENT) <= set(result)


@FAST
@given(st.dictionaries(st.sampled_from([*config_file._KEYS, "other", "1"]), JSONISH, max_size=6))
def test_config_file_any_document(document: dict[str, Any]) -> None:
    text = yaml.safe_dump(document)
    try:
        result = config_file.parse(text, CURRENT)
    except config_file.ConfigFileError:
        return
    assert isinstance(result["environment"], list) and all(isinstance(e["value"], str) for e in result["environment"])


def test_config_file_alias_bomb_is_cheap() -> None:
    lines = ['a: &a ["x","x","x","x","x","x","x","x","x"]']
    for i in range(1, 9):
        prev, cur = chr(ord("a") + i - 1), chr(ord("a") + i)
        lines.append(f"{cur}: &{cur} [{','.join(['*' + prev] * 9)}]")
    lines.append("environment: *i")
    with pytest.raises(config_file.ConfigFileError):
        config_file.parse("\n".join(lines), CURRENT)


# ----- validators: any text gives a message or None, never an exception -----

VALIDATORS = [
    fn
    for name, fn in inspect.getmembers(validation, inspect.isfunction)
    if fn.__module__ == "app.validation" and not name.startswith("_")
]


@pytest.mark.parametrize("validator", VALIDATORS, ids=lambda f: f.__name__)
@FAST
@given(value=URLISH)
def test_validators_never_raise(validator: Any, value: str) -> None:
    params = inspect.signature(validator).parameters
    kwargs: dict[str, Any] = {}
    if "resolver" in params:
        kwargs["resolver"] = lambda host: ["8.8.8.8"]
    args: list[Any] = [value]
    if "label" in params:
        args.append("Field")
    if validator.__name__ == "plain_credential":
        args = ["NAME", value]
    result = validator(*args, **kwargs)
    assert result is None or isinstance(result, str | bool)


@FAST
@given(URLISH)
def test_url_helpers_never_raise(url: str) -> None:
    parts = split_url(url)
    if parts is not None:
        _ = parts.hostname, parts.port
    problem = url_problem("webhook", url, resolver=lambda host: ["8.8.8.8"])
    assert problem is None or isinstance(problem, str)
    assert isinstance(display_host(url), str)
    assert check(url, "/", lambda host: False).outcome == "failed"


@FAST
@given(URLISH)
def test_alert_urls_resolving_privately_are_refused(url: str) -> None:
    assert url_problem("webhook", url, resolver=lambda host: ["10.1.2.3"]) is not None


# ----- redaction -----

SECRET = st.one_of(
    st.from_regex(r"AKIA[A-Z0-9]{16}", fullmatch=True),
    st.from_regex(r"ghp_[A-Za-z0-9]{36}", fullmatch=True),
    st.from_regex(r"ya29\.[A-Za-z0-9_-]{30}", fullmatch=True),
)
SEPARATOR = st.sampled_from([" ", "\n", "\t", ", ", "; ", "(", ")", "'", '"'])


@FAST
@given(ANY_TEXT, SEPARATOR, SECRET, SEPARATOR, ANY_TEXT)
def test_redaction_removes_embedded_credentials(before: str, sep1: str, secret: str, sep2: str, after: str) -> None:
    text = f"{before}{sep1}{secret}{sep2}{after}"
    assert secret not in redact(text)
    assert secret not in log_line(text)
    assert secret not in safe_message(text, "fallback")


@FAST
@given(ANY_TEXT)
def test_redaction_is_stable_and_bounded(text: str) -> None:
    once = redact(text)
    assert redact(once) == once
    assert len(log_line(text * 20)) <= MAX_LOG_LINE
    message = safe_message(text, "fallback")
    assert "\n" not in message and len(message) <= MAX_MESSAGE + 1


@FAST
@given(st.text(alphabet=string.ascii_letters + string.digits, min_size=6, max_size=30), ANY_TEXT)
def test_known_sensitive_values_are_removed(value: str, text: str) -> None:
    assert value not in log_line(f"{text} {value} {text}", [value]) or value in REDACTED


# ----- sleep schedules -----

ZONES = ["UTC", "Asia/Kolkata", "America/New_York", "Europe/London", "Australia/Lord_Howe", "Pacific/Chatham"]
TIMES = st.times().map(lambda t: t.replace(second=0, microsecond=0))


@FAST
@given(
    st.sampled_from(ZONES),
    st.sets(st.integers(0, 6), min_size=1),
    TIMES,
    TIMES,
    st.datetimes(min_value=dt.datetime(2000, 1, 1), max_value=dt.datetime(2100, 1, 1), timezones=st.just(dt.UTC)),
)
def test_schedule_matches_a_brute_force_model(
    zone: str, days: set[int], sleep_at: dt.time, wake_at: dt.time, moment: dt.datetime
) -> None:
    if sleep_at == wake_at:
        return
    schedule, errors = schedules.parse(zone, sorted(days), sleep_at.strftime("%H:%M"), wake_at.strftime("%H:%M"))
    assert schedule is not None and not errors
    local = moment.astimezone(dt.UTC).astimezone(__import__("zoneinfo").ZoneInfo(zone))
    now = local.time().replace(second=0, microsecond=0)
    today, yesterday = local.weekday(), (local.weekday() - 1) % 7
    if sleep_at < wake_at:
        expected = today in days and sleep_at <= now < wake_at
    else:
        expected = (today in days and now >= sleep_at) or (yesterday in days and now < wake_at)
    assert schedule.asleep_at(moment) is expected
    assert schedules.Schedule.from_json(schedule.to_json()) == schedule


@FAST
@given(JSONISH)
def test_schedule_from_any_json_never_raises(data: Any) -> None:
    result = schedules.Schedule.from_json(data if isinstance(data, dict) else None)
    assert result is None or isinstance(result, schedules.Schedule)


# ----- GitHub webhooks -----


@FAST
@given(ANY_TEXT, st.binary(max_size=200), st.one_of(st.none(), ANY_TEXT))
def test_webhook_signature_check_never_raises(secret: str, body: bytes, header: str | None) -> None:
    assert _signature_ok(secret, body, header) in (True, False)


@FAST
@given(st.from_regex(r"[a-z0-9-]{1,20}/[a-z0-9._-]{1,20}", fullmatch=True))
def test_repository_url_forms_compare_equal(slug: str) -> None:
    forms = [
        f"https://github.com/{slug}",
        f"https://github.com/{slug}.git",
        f"https://GitHub.com/{slug}/",
        f"git@github.com:{slug}.git",
        f"ssh://git@github.com/{slug}.git",
    ]
    assert len({_repo_key(f) for f in forms}) == 1
