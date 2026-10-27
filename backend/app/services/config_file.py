"""`cloudbridge.yaml`: an application's settings kept in its repository.

The file describes the requested configuration only (port, size, scaling, exposure, health check,
variables and secret references). Where to deploy and with which connection stay in CloudBridge.

    port: 8080
    cpu: "0.5"
    memory: 1Gi
    instances: {min: 1, max: 3}
    concurrency: 50
    exposure: public
    health_check_path: /healthz
    environment:
      LOG_LEVEL: info
    secrets:
      DB_PASSWORD: arn:aws:secretsmanager:...
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import yaml

from app.errors import FieldError

MAX_BYTES = 64 * 1024
FILE_NAME = "cloudbridge.yaml"
_KEYS = {
    "port",
    "cpu",
    "memory",
    "instances",
    "concurrency",
    "exposure",
    "health_check_path",
    "environment",
    "secrets",
}


class ConfigFileError(Exception):
    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("; ".join(e.message for e in errors))
        self.errors = errors


def parse(text: str, current: dict[str, Any]) -> dict[str, Any]:
    """Merges the file over `current` (a spec snapshot) and returns the requested configuration.

    Only syntax and types are checked here; provider rules run afterwards like for any other change.
    """
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ConfigFileError([FieldError("config", f"{FILE_NAME} is larger than 64 KB.")])
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise ConfigFileError([FieldError("config", f"{FILE_NAME} is not valid YAML{where}.")]) from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigFileError([FieldError("config", f"{FILE_NAME} must be a mapping of settings.")])

    errors: list[FieldError] = []
    unknown = sorted(str(k) for k in data if k not in _KEYS)
    if unknown:
        errors.append(FieldError("config", f"Unknown settings in {FILE_NAME}: {', '.join(unknown)}."))

    result: dict[str, Any] = {
        key: current[key]
        for key in (
            "port",
            "cpu",
            "memory",
            "min_instances",
            "max_instances",
            "concurrency",
            "exposure",
            "health_check_path",
        )
    }
    result["environment"] = [dict(e) for e in current.get("environment", [])]
    result["secret_refs"] = [dict(s) for s in current.get("secret_refs", [])]

    def integer(key: str, value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(FieldError(key, f"{key} must be a whole number."))
            return None
        return value

    if "port" in data and (port := integer("port", data["port"])) is not None:
        result["port"] = port
    for key in ("cpu", "memory"):
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, str | int | float):
                errors.append(FieldError(key, f'{key} must be a value such as "0.5" or "1Gi".'))
            else:
                result[key] = f"{value:g}" if isinstance(value, float) else str(value)
    if "instances" in data:
        instances = data["instances"]
        if not isinstance(instances, dict):
            errors.append(FieldError("instances", "instances must be a mapping with min and max."))
        else:
            for bound in ("min", "max"):
                if bound in instances and (value := integer(f"instances.{bound}", instances[bound])) is not None:
                    result[f"{bound}_instances"] = value
    if "concurrency" in data:
        value = data["concurrency"]
        result["concurrency"] = None if value is None else integer("concurrency", value)
    if "exposure" in data:
        if data["exposure"] not in ("public", "private"):
            errors.append(FieldError("exposure", "exposure must be public or private."))
        else:
            result["exposure"] = data["exposure"]
    if "health_check_path" in data:
        value = data["health_check_path"]
        if value is not None and not isinstance(value, str):
            errors.append(FieldError("health_check_path", "health_check_path must be a path such as /healthz."))
        else:
            result["health_check_path"] = value
    for key, target in (("environment", "environment"), ("secrets", "secret_refs")):
        if key not in data:
            continue
        values = data[key]
        if values is None:
            result[target] = []
            continue
        if not isinstance(values, dict):
            errors.append(FieldError(key, f"{key} must be a mapping of NAME: value."))
            continue
        rows = []
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, str | int | float):
                errors.append(FieldError(key, f"{key}.{name} must be text."))
                continue
            text_value = str(value)
            rows.append(
                {"name": str(name), "value": text_value}
                if target == "environment"
                else {"name": str(name), "reference": text_value}
            )
        result[target] = rows
    if errors:
        raise ConfigFileError(errors)
    return result


class ConfigFetchError(Exception):
    """The repository could not be read (as opposed to the file simply not existing)."""


_GITHUB_REPO = re.compile(r"(?a)^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def fetch_github(repository_url: str, ref: str, client_factory: Callable[[], httpx.Client] | None = None) -> str | None:
    """The file's text at `ref`, or None when the repository has no such file.

    Only public github.com repositories are read, through raw.githubusercontent.com: the host is fixed,
    so a repository URL cannot make CloudBridge fetch from anywhere else.
    """
    parts = urlsplit(repository_url)
    match = _GITHUB_REPO.match(parts.path)
    if parts.scheme != "https" or (parts.hostname or "").lower() != "github.com" or match is None:
        raise ConfigFetchError(f"{FILE_NAME} can be read only from public github.com repositories.")
    owner, repo = match.group(1), match.group(2)
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(ref, safe='/')}/{FILE_NAME}"
    factory = client_factory or (lambda: httpx.Client(timeout=15.0, follow_redirects=False))
    try:
        with factory() as client:
            response = client.get(url, headers={"User-Agent": "CloudBridge/1"})
    except httpx.HTTPError:
        raise ConfigFetchError(f"GitHub could not be reached to read {FILE_NAME}.") from None
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ConfigFetchError(f"GitHub answered {response.status_code} when reading {FILE_NAME}.")
    if len(response.content) > MAX_BYTES:
        raise ConfigFetchError(f"{FILE_NAME} is larger than 64 KB.")
    return response.text
