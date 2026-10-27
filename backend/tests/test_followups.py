"""Regression tests for fixes that previously had none: e-mail after commit, internal-error counter,
the delete race, odd resolver output, the CLI organization name and App Runner's scaling quota."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from sqlalchemy import update

from app import cli, clock, validation
from app.db import Base, Database
from app.models import Application, Deployment, Organization, User
from app.security import hash_password
from app.worker.notify import Notifier
from tests.conftest import ApiClient, Env, make_settings
from tests.test_deployments import create_app, deploy, run_until_done


def _org_with_user(database: Database) -> str:
    with database.session() as db:
        org = Organization(name="Mail", created_at=clock.utcnow())
        db.add(org)
        db.flush()
        db.add(
            User(
                organization_id=org.id,
                email="ops@example.com",
                display_name="Ops",
                role="owner",
                password_hash=hash_password("correct horse battery staple"),
                time_format="24h",
                created_at=clock.utcnow(),
            )
        )
        db.commit()
        return org.id


def test_notifications_are_sent_only_after_the_change_is_committed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, smtp_host="smtp.example.com", smtp_from="cloudbridge@example.com")
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    org_id = _org_with_user(database)
    notifier = Notifier(settings)
    sent: list[tuple[list[str], str]] = []
    notifier._send = lambda recipients, subject, body: sent.append((recipients, subject))  # type: ignore[method-assign]

    with database.session() as db:
        notifier.notify(db, org_id, "deployment_failed", "rolled back", "body")
        assert sent == []  # not before the commit
        db.rollback()
        db.commit()
    assert sent == []  # dropped with the rollback

    with database.session() as db:
        notifier.notify(db, org_id, "deployment_failed", "failed", "body")
        db.commit()
    notifier.close()
    assert sent == [(["ops@example.com"], "failed")]
    database.dispose()


def test_internal_error_count_resets_after_a_successful_step(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    dep = deploy(owner, app["id"])
    with env.database.session() as db:
        db.execute(update(Deployment).values(state={"internal_errors": 2}))
        db.commit()
    env.run_worker()
    with env.database.session() as db:
        row = db.get(Deployment, dep["id"])
        assert row is not None and "internal_errors" not in (row.state or {})


def test_delete_loses_cleanly_to_a_deployment_that_started_meanwhile(
    env: Env, owner: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    from app.routers import applications

    original = applications.find_app

    def stale_find(db: Any, principal: Any, application_id: str) -> Application:
        found = original(db, principal, application_id)
        with env.database.session() as other:  # a deployment claims the app after the read
            other.execute(update(Application).values(active_deployment_id="dep-racing"))
            other.commit()
        return found

    monkeypatch.setattr(applications, "find_app", stale_find)
    response = owner.delete(f"/apps/{app['id']}")
    assert response.status_code == 409 and response.json()["error"]["code"] == "deployment_in_progress"
    with env.database.session() as db:
        row = db.get(Application, app["id"])
        assert row is not None and row.delete_requested_at is None and row.status != "deleting"


def test_repository_host_check_handles_unusual_resolver_output() -> None:
    url = "https://git.example.com/acme/repo.git"
    assert validation.repository_host_is_public(url, lambda host: ["not-an-address"]) is not None
    assert "private" in str(validation.repository_host_is_public(url, lambda host: ["fe80::1%eth0"]))
    assert validation.repository_host_is_public(url, lambda host: ["140.82.112.3"]) is None


def test_cli_rejects_an_empty_organization_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    database = Database(url)
    Base.metadata.create_all(database.engine)
    database.dispose()
    monkeypatch.setenv("CLOUDBRIDGE_DATABASE_URL", url)
    monkeypatch.setenv("CLOUDBRIDGE_BOOTSTRAP_PASSWORD", "correct horse battery staple")
    with pytest.raises(SystemExit) as raised:
        cli.main(
            ["create-user", "--email", "a@example.com", "--name", "A", "--role", "owner", "--new-organization", "  "]
        )
    assert "organization name" in str(raised.value)


SCALING_ARN = "arn:aws:apprunner:eu-west-1:123456789012:autoscalingconfiguration/{}/1/0123456789abcdef"


def test_scaling_quota_frees_unused_cloudbridge_configurations(tmp_path: Path) -> None:
    import boto3

    from app.providers.aws import AwsAppRunnerAdapter
    from app.providers.base import SpecInfo

    client: Any = boto3.Session(aws_access_key_id="x", aws_secret_access_key="y").client(
        "apprunner", region_name="eu-west-1"
    )
    stub = Stubber(client)
    name = "cloudbridge-1-3-100"
    stub.add_response(
        "list_auto_scaling_configurations",
        {"AutoScalingConfigurationSummaryList": []},
        {"AutoScalingConfigurationName": name, "LatestOnly": True},
    )
    stub.add_client_error(
        "create_auto_scaling_configuration", service_error_code="ServiceQuotaExceededException", http_status_code=402
    )

    def summary(config: str, used: bool, default: bool = False) -> dict[str, Any]:
        return {
            "AutoScalingConfigurationArn": SCALING_ARN.format(config),
            "AutoScalingConfigurationName": config,
            "Status": "active",
            "HasAssociatedService": used,
            "IsDefault": default,
        }

    stub.add_response(
        "list_auto_scaling_configurations",
        {
            "AutoScalingConfigurationSummaryList": [
                summary("cloudbridge-1-1-100", used=False),  # freed
                summary("cloudbridge-2-5-100", used=True),  # a service uses it: kept
                summary("customer-config", used=False),  # not CloudBridge's: kept
                summary("DefaultConfiguration", used=False, default=True),
            ]
        },
        {"MaxResults": 100},
    )
    stub.add_response(
        "delete_auto_scaling_configuration",
        {
            "AutoScalingConfiguration": {
                "AutoScalingConfigurationArn": SCALING_ARN.format("cloudbridge-1-1-100"),
                "Status": "inactive",
            }
        },
        {"AutoScalingConfigurationArn": SCALING_ARN.format("cloudbridge-1-1-100")},
    )
    stub.add_response(
        "create_auto_scaling_configuration",
        {"AutoScalingConfiguration": {"AutoScalingConfigurationArn": SCALING_ARN.format(name)}},
    )
    spec = SpecInfo(
        port=8080, cpu="1", memory="2Gi", min_instances=1, max_instances=3, concurrency=None, exposure="public",
        health_check_path=None, environment=[], secret_refs=[],
    )  # fmt: skip
    with stub:
        arn = AwsAppRunnerAdapter(make_settings(tmp_path))._autoscaling_arn(client, spec)
    assert arn == SCALING_ARN.format(name)
    stub.assert_no_pending_responses()


def test_scaling_quota_with_nothing_to_free_reports_the_quota(tmp_path: Path) -> None:
    import boto3

    from app.providers.aws import AwsAppRunnerAdapter
    from app.providers.base import SpecInfo

    client: Any = boto3.Session(aws_access_key_id="x", aws_secret_access_key="y").client(
        "apprunner", region_name="eu-west-1"
    )
    stub = Stubber(client)
    stub.add_response("list_auto_scaling_configurations", {"AutoScalingConfigurationSummaryList": []})
    stub.add_client_error("create_auto_scaling_configuration", service_error_code="ServiceQuotaExceededException")
    stub.add_response("list_auto_scaling_configurations", {"AutoScalingConfigurationSummaryList": []})
    spec = SpecInfo(
        port=8080, cpu="1", memory="2Gi", min_instances=1, max_instances=3, concurrency=None, exposure="public",
        health_check_path=None, environment=[], secret_refs=[],
    )  # fmt: skip
    with stub, pytest.raises(ClientError):
        AwsAppRunnerAdapter(make_settings(tmp_path))._autoscaling_arn(client, spec)


def test_timestamps_are_strictly_increasing() -> None:
    import itertools

    from app import clock

    stamps = [clock.utcnow() for _ in range(2000)]
    assert all(a < b for a, b in itertools.pairwise(stamps))
