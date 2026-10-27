#!/usr/bin/env python3
"""Connect a cloud account to CloudBridge, step by step, with your approval.

Run it with your own signed-in AWS CLI, Azure CLI or kubectl: in AWS CloudShell, Azure Cloud Shell
or any terminal. It shows every change before making it and asks you first. It never creates access
keys, client secrets or any other long-lived credential, and it sends nothing to CloudBridge: at the
end it prints the values to enter in CloudBridge.

    python3 cloudbridge-connect.py aws --help         AWS App Runner or ECS Express
    python3 cloudbridge-connect.py azure --help       Azure Container Apps
    python3 cloudbridge-connect.py aks --help         Azure Kubernetes Service
    python3 cloudbridge-connect.py kubernetes --help  any other cluster (OIDC)

CloudBridge's connection page shows the full command with your values filled in.
Add --dry-run to see every step without changing anything.

Needs Python 3.9+ (AWS CloudShell and Azure Cloud Shell have it) and the provider's command-line tool.
Uses only the standard library.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Callable, Sequence
from typing import Any

AZURE_AUDIENCE = "api://AzureADTokenExchange"
AWS_DEFAULT_ROLES = {
    "apprunner_access_role": "CloudBridgeAppRunnerECRAccess",
    "apprunner_instance_role": "CloudBridgeAppRunnerInstance",
    "codebuild_role": "CloudBridgeCodeBuild",
    "ecs_execution_role": "CloudBridgeECSExecution",
    "ecs_infrastructure_role": "CloudBridgeECSInfrastructure",
}
MANAGED_TAG = "managed-by=cloudbridge"
RUNTIME_IDENTITY = "cloudbridge-runtime"


class Abort(Exception):
    """Stops the script before anything else changes."""


class CommandError(Exception):
    def __init__(self, args: Sequence[str], code: int, stderr: str) -> None:
        super().__init__(stderr.strip() or f"exit code {code}")
        self.args_list = list(args)
        self.code = code
        self.stderr = stderr


# ---------------------------------------------------------------------------- running commands


class Runner:
    """Runs the cloud command-line tools. Reads always run; changes only run outside a dry run."""

    def __init__(self, dry_run: bool, out: Callable[[str], None] = print) -> None:
        self.dry_run = dry_run
        self.out = out
        self._tmp = tempfile.TemporaryDirectory(prefix="cloudbridge-")
        self._files = 0

    def close(self) -> None:
        self._tmp.cleanup()

    def tool(self, name: str, hint: str) -> None:
        if shutil.which(name) is None:
            raise Abort(f"'{name}' was not found. {hint}")

    def json_file(self, document: Any) -> str:
        """Writes a JSON document to a private temporary file (no quoting problems on any shell)."""
        self._files += 1
        path = os.path.join(self._tmp.name, f"doc{self._files}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
        return path

    def _exec(self, args: Sequence[str], stdin: str | None) -> str:
        executable = shutil.which(args[0]) or args[0]
        try:
            done = subprocess.run(  # noqa: S603 - argument lists built by this script, never a shell
                [executable, *args[1:]],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as error:
            raise CommandError(args, 127, str(error)) from None
        if done.returncode != 0:
            raise CommandError(args, done.returncode, done.stderr)
        return done.stdout

    def read(self, args: Sequence[str], stdin: str | None = None) -> str:
        return self._exec(args, stdin)

    def read_json(self, args: Sequence[str]) -> Any:
        text = self.read(args).strip()
        return json.loads(text) if text else None

    def change(self, args: Sequence[str], stdin: str | None = None) -> str:
        if self.dry_run:
            raise AssertionError("changes never run in a dry run")
        return self._exec(args, stdin)

    def change_json(self, args: Sequence[str]) -> Any:
        text = self.change(args).strip()
        return json.loads(text) if text else None


def missing(error: CommandError, *markers: str) -> bool:
    text = error.stderr
    return any(marker in text for marker in markers)


# ---------------------------------------------------------------------------- steps


class Step:
    """One approved change. `apply` returns a short result line; `blocked` says why it cannot run yet."""

    def __init__(
        self,
        title: str,
        details: list[str],
        apply: Callable[[], str],
        *,
        optional: bool = False,
        done: str | None = None,
        blocked: Callable[[], str | None] | None = None,
    ) -> None:
        self.title = title
        self.details = details
        self.apply = apply
        self.optional = optional
        self.done = done
        self.blocked = blocked or (lambda: None)


class Plan:
    def __init__(self, heading: list[str]) -> None:
        self.heading = heading
        self.steps: list[Step] = []
        self.finish: Callable[[dict[str, str]], list[str]] = lambda _results: []

    def add(self, step: Step) -> None:
        self.steps.append(step)


Ask = Callable[[str], str]


def run_plan(plan: Plan, runner: Runner, ask: Ask) -> int:
    out = runner.out
    for line in plan.heading:
        out(line)
    out("")
    out("This script will:")
    for number, step in enumerate(plan.steps, 1):
        suffix = " (already done)" if step.done else (" (optional)" if step.optional else "")
        out(f"  {number}. {step.title}{suffix}")
    out("")
    if runner.dry_run:
        out("Dry run: nothing will be changed.")
    else:
        out("You will be asked before each step. Answer y to do it, n to skip it, or q to stop.")

    results: dict[str, str] = {}
    failed = skipped = 0
    for number, step in enumerate(plan.steps, 1):
        out("")
        out(f"[{number}/{len(plan.steps)}] {step.title}")
        if step.done:
            out(f"  Already done: {step.done}")
            results[step.title] = "done"
            continue
        for line in step.details:
            out(f"  {line}")
        if runner.dry_run:
            out("  Dry run: not changed.")
            results[step.title] = "dry-run"
            continue
        reason = step.blocked()  # from what earlier steps did; never calls the cloud
        if reason:
            out(f"  Skipped: {reason}")
            results[step.title] = "blocked"
            skipped += 1
            continue
        answer = _answer(ask, "  Do this step? [y/n/q] ", ("y", "n", "q"))
        if answer == "q":
            out("Stopped. Steps already done stay in place; run the script again to continue.")
            return 1
        if answer == "n":
            if not step.optional:
                out("  Skipped. CloudBridge cannot connect until this is done.")
            else:
                out("  Skipped.")
            results[step.title] = "skipped"
            skipped += 1
            continue
        try:
            outcome = step.apply()
        except CommandError as error:
            failed += 1
            results[step.title] = "failed"
            out(f"  Failed: {_first_lines(str(error))}")
            if number < len(plan.steps) and _answer(ask, "  Continue with the next step? [y/n] ", ("y", "n")) == "n":
                out("Stopped. Steps already done stay in place; run the script again to continue.")
                return 1
            continue
        results[step.title] = "done"
        out(f"  Done. {outcome}".rstrip())

    out("")
    if runner.dry_run:
        out("Dry run finished. Run the same command without --dry-run to make these changes.")
        return 0
    for line in plan.finish(results):
        out(line)
    if failed or skipped:
        out("")
        out(
            f"{failed} step(s) failed and {skipped} were skipped. Fix them and run the script again; "
            "steps already done are kept."
        )
        return 1
    return 0


def _answer(ask: Ask, prompt: str, choices: tuple[str, ...]) -> str:
    while True:
        try:
            reply = ask(prompt).strip().lower()
        except EOFError:
            return "q" if "q" in choices else "n"
        if reply[:1] in choices:
            return reply[:1]


def _first_lines(text: str, limit: int = 6) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n          ".join(lines[:limit]) or "unknown error"


def describe_policy(document: dict[str, Any]) -> list[str]:
    """Every statement of an IAM policy in one line: what it allows, on what, under which condition."""
    lines = []
    for statement in document["Statement"]:
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        resources = statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]]
        line = f"- {', '.join(actions)} on {', '.join(resources)}"
        if "Condition" in statement:
            line += f" when {json.dumps(statement['Condition'], separators=(',', ':'))}"
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------- AWS


def parse_role_arn(arn: str) -> tuple[str, str, str, str]:
    """Returns (partition, account, path, name) for arn:aws:iam::123456789012:role/optional/path/Name."""
    match = re.fullmatch(r"arn:(aws[a-z-]*):iam::(\d{12}):role(/(?:[\w+=,.@-]+/)*)([\w+=,.@-]{1,64})", arn)
    if not match:
        raise Abort(f"'{arn}' is not an IAM role ARN like arn:aws:iam::123456789012:role/CloudBridgeDeploy.")
    return match.group(1), match.group(2), match.group(3), match.group(4)


def trust(service: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}],
    }


def connection_trust(principal: str, external_id: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": principal},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": external_id}},
            }
        ],
    }


def _allow(sid: str, actions: list[str], resources: Any, condition: dict[str, Any] | None = None) -> dict[str, Any]:
    statement: dict[str, Any] = {"Sid": sid, "Effect": "Allow", "Action": actions, "Resource": resources}
    if condition:
        statement["Condition"] = condition
    return statement


def connection_policy(
    mode: str, partition: str, account: str, roles: dict[str, str], ecr_prefix: str
) -> dict[str, Any]:
    """What CloudBridge calls in the account. Keep in step with backend/app/providers/aws.py and ecs.py."""
    repositories = f"arn:{partition}:ecr:*:{account}:repository/{ecr_prefix}/*"
    projects = f"arn:{partition}:codebuild:*:{account}:project/cloudbridge-*"

    def role(name: str) -> str:
        return f"arn:{partition}:iam::{account}:role/{name}"

    statements: list[dict[str, Any]] = []
    if mode == "apprunner":
        statements.append(
            _allow(
                "AppRunner",
                [
                    "apprunner:CreateService",
                    "apprunner:UpdateService",
                    "apprunner:DescribeService",
                    "apprunner:DeleteService",
                    "apprunner:ListServices",
                    "apprunner:ListOperations",
                    "apprunner:TagResource",
                    "apprunner:ListTagsForResource",
                    "apprunner:CreateAutoScalingConfiguration",
                    "apprunner:DescribeAutoScalingConfiguration",
                    "apprunner:ListAutoScalingConfigurations",
                    "apprunner:DeleteAutoScalingConfiguration",
                    "apprunner:AssociateCustomDomain",
                    "apprunner:DescribeCustomDomains",
                    "apprunner:DisassociateCustomDomain",
                    "apprunner:PauseService",
                    "apprunner:ResumeService",
                ],
                "*",
            )
        )
        passed = [roles["apprunner_access_role"], roles["apprunner_instance_role"], roles["codebuild_role"]]
        linked = ["apprunner.amazonaws.com"]
    else:
        statements.append(
            _allow(
                "EcsExpress",
                [
                    "ecs:CreateExpressGatewayService",
                    "ecs:UpdateExpressGatewayService",
                    "ecs:DescribeExpressGatewayService",
                    "ecs:DeleteExpressGatewayService",
                    "ecs:CreateCluster",
                    "ecs:RegisterTaskDefinition",
                    "ecs:DeregisterTaskDefinition",
                    "ecs:DescribeTaskDefinition",
                    "ecs:CreateService",
                    "ecs:UpdateService",
                    "ecs:DeleteService",
                    "ecs:DescribeServices",
                    "ecs:ListServices",
                    "ecs:DescribeServiceDeployments",
                    "ecs:ListServiceDeployments",
                    "ecs:ListClusters",
                    "ecs:DescribeClusters",
                    "ecs:TagResource",
                    "ecs:UntagResource",
                    "ecs:ListTagsForResource",
                ],
                "*",
            )
        )
        passed = [roles["ecs_execution_role"], roles["ecs_infrastructure_role"], roles["codebuild_role"]]
        linked = ["ecs.amazonaws.com", "elasticloadbalancing.amazonaws.com"]
    statements += [
        _allow("FindImages", ["ecr:DescribeRepositories", "ecr:DescribeImages"], "*"),
        _allow(
            "BuildRepositories",
            ["ecr:CreateRepository", "ecr:TagResource", "ecr:ListTagsForResource", "ecr:DeleteRepository"],
            repositories,
        ),
        _allow("ListBuildProjects", ["codebuild:ListProjects"], "*"),
        _allow(
            "BuildProjects",
            [
                "codebuild:CreateProject",
                "codebuild:UpdateProject",
                "codebuild:BatchGetProjects",
                "codebuild:DeleteProject",
                "codebuild:StartBuild",
                "codebuild:StopBuild",
                "codebuild:BatchGetBuilds",
            ],
            projects,
        ),
        _allow("LogsAndMetrics", ["logs:GetLogEvents", "logs:FilterLogEvents", "cloudwatch:GetMetricData"], "*"),
        _allow("PassServiceRoles", ["iam:PassRole"], [role(name) for name in passed]),
        _allow(
            "FirstServiceInAccount",
            ["iam:CreateServiceLinkedRole"],
            "*",
            {"StringEquals": {"iam:AWSServiceName": linked if len(linked) > 1 else linked[0]}},
        ),
    ]
    return {"Version": "2012-10-17", "Statement": statements}


def codebuild_policy(partition: str, account: str, ecr_prefix: str) -> dict[str, Any]:
    """Lets CloudBridge's builds push to its own repositories and write their logs, nothing else."""
    logs = f"arn:{partition}:logs:*:{account}:log-group:/aws/codebuild/cloudbridge-*"
    return {
        "Version": "2012-10-17",
        "Statement": [
            _allow("RegistryLogin", ["ecr:GetAuthorizationToken"], "*"),
            _allow(
                "PushImages",
                [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                ],
                f"arn:{partition}:ecr:*:{account}:repository/{ecr_prefix}/*",
            ),
            _allow(
                "BuildLogs", ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], [logs, f"{logs}:*"]
            ),
        ],
    }


class Aws:
    def __init__(self, runner: Runner) -> None:
        self.r = runner

    def get_role(self, name: str) -> dict[str, Any] | None:
        try:
            found = self.r.read_json(["aws", "iam", "get-role", "--role-name", name, "--output", "json"])
        except CommandError as error:
            if missing(error, "NoSuchEntity"):
                return None
            raise
        role: dict[str, Any] = found["Role"]
        return role

    def create_role(self, name: str, path: str, trust_policy: dict[str, Any], description: str) -> None:
        self.r.change(
            [
                "aws", "iam", "create-role",
                "--role-name", name,
                "--path", path,
                "--description", description,
                "--assume-role-policy-document", "file://" + self.r.json_file(trust_policy),
                "--tags", "Key=managed-by,Value=cloudbridge",
                "--output", "json",
            ]
        )  # fmt: skip

    def set_trust(self, name: str, trust_policy: dict[str, Any]) -> None:
        self.r.change(
            [
                "aws", "iam", "update-assume-role-policy",
                "--role-name", name,
                "--policy-document", "file://" + self.r.json_file(trust_policy),
            ]
        )  # fmt: skip

    def put_policy(self, role: str, policy_name: str, policy: dict[str, Any]) -> None:
        self.r.change(
            [
                "aws", "iam", "put-role-policy",
                "--role-name", role,
                "--policy-name", policy_name,
                "--policy-document", "file://" + self.r.json_file(policy),
            ]
        )  # fmt: skip

    def attach_managed(self, role: str, partition: str, policy_name: str) -> str:
        """AWS keeps some managed policies under /service-role/; look in both places."""
        for arn in (
            f"arn:{partition}:iam::aws:policy/service-role/{policy_name}",
            f"arn:{partition}:iam::aws:policy/{policy_name}",
        ):
            try:
                self.r.read(["aws", "iam", "get-policy", "--policy-arn", arn, "--output", "json"])
            except CommandError as error:
                if missing(error, "NoSuchEntity"):
                    continue
                raise
            self.r.change(["aws", "iam", "attach-role-policy", "--role-name", role, "--policy-arn", arn])
            return arn
        raise CommandError(["aws", "iam", "get-policy"], 1, f"The AWS managed policy {policy_name} was not found.")


def aws_plan(args: argparse.Namespace, runner: Runner) -> Plan:
    partition, account, path, name = parse_role_arn(args.role_arn)
    if args.account and args.account != account:
        raise Abort(f"The role ARN is in account {account}, but the connection is for account {args.account}.")
    if not args.external_id.strip() or not args.principal.startswith("arn:"):
        raise Abort("--principal must be CloudBridge's role ARN and --external-id the connection's external ID.")
    runner.tool("aws", "Run this in AWS CloudShell, or install the AWS CLI and sign in (aws login).")
    aws = Aws(runner)
    try:
        identity = runner.read_json(["aws", "sts", "get-caller-identity", "--output", "json"])
    except CommandError as error:
        raise Abort(f"The AWS CLI is not signed in: {_first_lines(str(error), 2)}") from None
    if identity.get("Account") != account:
        raise Abort(
            f"You are signed in to AWS account {identity.get('Account')}, but the role belongs to account {account}. "
            "Sign in to the right account (or open CloudShell there) and run the script again."
        )

    mode = "ecs" if args.ecs else "apprunner"
    roles = {key: getattr(args, key) for key in AWS_DEFAULT_ROLES}
    label = "ECS Express" if args.ecs else "App Runner"
    plan = Plan(
        [
            f"CloudBridge setup for AWS {label}",
            f"  Signed in as {identity.get('Arn')}",
            f"  Account {account}; CloudBridge will assume role {name} with external ID {args.external_id}",
        ]
    )

    existing = aws.get_role(name)
    if existing is not None and existing.get("Arn") != args.role_arn:
        raise Abort(f"A role named {name} already exists as {existing.get('Arn')}, not {args.role_arn}.")
    has_role = {"value": existing is not None}
    wanted_trust = connection_trust(args.principal, args.external_id)
    if existing is None:

        def create_connection_role() -> str:
            aws.create_role(name, path, wanted_trust, "Lets CloudBridge deploy applications (external ID required)")
            has_role["value"] = True
            return f"Created role {name}."

        plan.add(
            Step(
                f"Create IAM role {name} for CloudBridge",
                [
                    f"Only {args.principal} may assume it, and only with external ID {args.external_id}.",
                    "No access keys are created.",
                ],
                create_connection_role,
            )
        )
    else:
        current = existing.get("AssumeRolePolicyDocument")
        same = isinstance(current, dict) and _same_trust(current, wanted_trust)

        def update_trust() -> str:
            aws.set_trust(name, wanted_trust)
            return f"Updated the trust policy of {name}."

        plan.add(
            Step(
                f"Let CloudBridge assume the existing role {name}",
                [
                    f"Replaces its trust policy: only {args.principal} may assume it,",
                    f"with external ID {args.external_id}.",
                    "Anyone else it trusts today (including another CloudBridge connection) loses access.",
                ],
                update_trust,
                done="its trust policy already allows only CloudBridge with this external ID" if same else None,
            )
        )

    permissions = connection_policy(mode, partition, account, roles, args.ecr_prefix)

    def grant_permissions() -> str:
        aws.put_policy(name, "CloudBridge", permissions)
        return f"Saved inline policy CloudBridge on {name}."

    plan.add(
        Step(
            f"Give {name} the permissions CloudBridge needs",
            ["Saves this inline policy, named CloudBridge, on the role (replacing one of that name):"]
            + ["  " + line for line in describe_policy(permissions)],
            grant_permissions,
            blocked=lambda: None if has_role["value"] else f"role {name} does not exist yet",
        )
    )

    def service_role(
        role_name: str, service: str, purpose: str, managed: str | None, inline: dict[str, Any] | None, optional: bool
    ) -> None:
        found = aws.get_role(role_name)
        details = [purpose, f"Trusted by {service} only; CloudBridge can only pass it to that service."]
        if found and found.get("Path", "/") != "/":
            details.append(f"Warning: it exists under path {found.get('Path')}; CloudBridge expects it at /.")
        if managed:
            details.append(f"Attaches the AWS managed policy {managed}.")
        if inline:
            details.append("Saves this inline policy, named CloudBridge:")
            details += ["  " + line for line in describe_policy(inline)]
        if found:
            details.insert(0, "The role already exists; its trust policy is left as it is.")

        def apply() -> str:
            created = False
            if aws.get_role(role_name) is None:
                aws.create_role(role_name, "/", trust(service), purpose)
                created = True
            if managed:
                aws.attach_managed(role_name, partition, managed)
            if inline:
                aws.put_policy(role_name, "CloudBridge", inline)
            return f"{'Created' if created else 'Updated'} role {role_name}."

        verb = "Update" if found else "Create"
        plan.add(Step(f"{verb} service role {role_name}", details, apply, optional=optional))

    if mode == "apprunner":
        service_role(
            roles["apprunner_access_role"],
            "build.apprunner.amazonaws.com",
            "App Runner uses it to pull private images from Amazon ECR.",
            "AWSAppRunnerServicePolicyForECRAccess",
            None,
            False,
        )
        service_role(
            roles["apprunner_instance_role"],
            "tasks.apprunner.amazonaws.com",
            "Your running applications use it. It has no permissions until you let it read your secrets.",
            None,
            None,
            False,
        )
    else:
        service_role(
            roles["ecs_execution_role"],
            "ecs-tasks.amazonaws.com",
            "ECS uses it to pull images and write logs for your applications.",
            "AmazonECSTaskExecutionRolePolicy",
            None,
            False,
        )
        service_role(
            roles["ecs_infrastructure_role"],
            "ecs.amazonaws.com",
            "ECS Express Mode uses it to create the load balancer and networking for each service.",
            "AmazonECSInfrastructureRoleforExpressGatewayServices",
            None,
            False,
        )
    service_role(
        roles["codebuild_role"],
        "codebuild.amazonaws.com",
        "CodeBuild uses it to build images from your Git repositories (not needed for image sources).",
        None,
        codebuild_policy(partition, account, args.ecr_prefix),
        True,
    )

    def finish(_results: dict[str, str]) -> list[str]:
        secrets = roles["ecs_execution_role"] if args.ecs else roles["apprunner_instance_role"]
        return [
            "All set. IAM changes can take a few seconds to apply everywhere.",
            "In CloudBridge, open the connection and select Validate connection.",
            f"To use secret references, let role {secrets} read those secrets (secretsmanager:GetSecretValue or "
            "ssm:GetParameters on them).",
        ]

    plan.finish = finish
    return plan


def _same_trust(current: dict[str, Any], wanted: dict[str, Any]) -> bool:
    statements = current.get("Statement")
    if not isinstance(statements, list) or len(statements) != 1:
        return False
    have, want = statements[0], wanted["Statement"][0]
    return (
        have.get("Effect") == "Allow"
        and have.get("Action") in ("sts:AssumeRole", ["sts:AssumeRole"])
        and have.get("Principal") == want["Principal"]
        and have.get("Condition") == want["Condition"]
    )


# ---------------------------------------------------------------------------- Azure


class Azure:
    def __init__(self, runner: Runner, subscription: str) -> None:
        self.r = runner
        self.subscription = subscription

    def az(self, *args: str) -> list[str]:
        return ["az", *args, "--only-show-errors", "--output", "json"]

    def sub(self, *args: str) -> list[str]:
        return self.az(*args, "--subscription", self.subscription)

    def show(self, *args: str) -> dict[str, Any] | None:
        try:
            found: dict[str, Any] | None = self.r.read_json(self.sub(*args))
            return found
        except CommandError as error:
            if missing(
                error, "ResourceNotFound", "ResourceGroupNotFound", "was not found", "could not be found", "NotFound"
            ):
                return None
            raise

    def assign(self, principal_id: str, role: str, scope: str) -> str:
        try:
            self.r.change(
                self.sub(
                    "role", "assignment", "create",
                    "--assignee-object-id", principal_id,
                    "--assignee-principal-type", "ServicePrincipal",
                    "--role", role,
                    "--scope", scope,
                )
            )  # fmt: skip
        except CommandError as error:
            if missing(error, "RoleAssignmentExists", "already exists"):
                return f"{role} was already assigned."
            raise
        return f"Assigned {role}."


class AppRegistration:
    """The app registration CloudBridge signs in as: an existing one (--client-id) or a new one."""

    def __init__(self, runner: Runner, azure: Azure, client_id: str | None, display_name: str) -> None:
        self.r = runner
        self.azure = azure
        self.display_name = display_name
        self.client_id: str | None = None
        self.object_id: str | None = None
        self.principal_id: str | None = None
        self.existing_credentials: list[dict[str, Any]] = []
        if client_id:
            try:
                app = runner.read_json(azure.az("ad", "app", "show", "--id", client_id))
            except CommandError as error:
                raise Abort(
                    f"App registration {client_id} was not found in this tenant: {_first_lines(str(error), 2)}"
                ) from None
            self.client_id = app["appId"]
            self.object_id = app["id"]
            self.display_name = app.get("displayName") or display_name
            self.existing_credentials = (
                runner.read_json(azure.az("ad", "app", "federated-credential", "list", "--id", app["id"])) or []
            )
            try:
                sp = runner.read_json(azure.az("ad", "sp", "show", "--id", app["appId"]))
                self.principal_id = sp["id"]
            except CommandError as error:
                if not missing(error, "does not exist", "not found", "NotFound", "Request_ResourceNotFound"):
                    raise

    def label(self) -> str:
        return f"app registration {self.display_name}" + (f" ({self.client_id})" if self.client_id else "")

    def need(self) -> str | None:
        return None if self.principal_id else "the app registration does not exist yet"

    def add_steps(self, plan: Plan, issuer: str, subject: str, audience: str) -> None:
        if self.object_id is None or self.principal_id is None:

            def create() -> str:
                if self.object_id is None:
                    app = self.r.change_json(
                        self.azure.az(
                            "ad",
                            "app",
                            "create",
                            "--display-name",
                            self.display_name,
                            "--sign-in-audience",
                            "AzureADMyOrg",
                        )
                    )
                    self.client_id, self.object_id = app["appId"], app["id"]
                sp = self.r.change_json(self.azure.az("ad", "sp", "create", "--id", str(self.client_id)))
                self.principal_id = sp["id"]
                return f"Application (client) ID: {self.client_id}"

            title = (
                f"Create app registration {self.display_name}"
                if self.object_id is None
                else f"Create the service principal of {self.label()}"
            )
            plan.add(
                Step(
                    title,
                    [
                        "CloudBridge signs in as this application through a federated credential.",
                        "No client secret or certificate is created.",
                    ],
                    create,
                )
            )

        trusted = any(
            c.get("issuer") == issuer and c.get("subject") == subject and audience in (c.get("audiences") or [])
            for c in self.existing_credentials
        )
        name = "cloudbridge-" + hashlib.sha256(f"{issuer}|{subject}".encode()).hexdigest()[:10]

        def federate() -> str:
            document = {
                "name": name,
                "issuer": issuer,
                "subject": subject,
                "audiences": [audience],
                "description": "Lets CloudBridge sign in without a secret",
            }
            try:
                self.r.change(
                    self.azure.az(
                        "ad", "app", "federated-credential", "create",
                        "--id", str(self.object_id),
                        "--parameters", "@" + self.r.json_file(document),
                    )
                )  # fmt: skip
            except CommandError as error:
                if missing(error, "already exists", "FederatedIdentityCredentialAlreadyExists"):
                    return "It was already there."
                raise
            return f"Added federated credential {name}."

        plan.add(
            Step(
                "Let CloudBridge sign in to the app registration",
                [f"Adds a federated credential: issuer {issuer}", f"subject {subject}", f"audience {audience}"],
                federate,
                done="it already trusts CloudBridge" if trusted else None,
                blocked=lambda: None if self.object_id else "the app registration does not exist yet",
            )
        )


def azure_session(args: argparse.Namespace, runner: Runner) -> tuple[Azure, dict[str, Any]]:
    runner.tool("az", "Run this in Azure Cloud Shell, or install the Azure CLI and sign in (az login).")
    try:
        account = runner.read_json(
            ["az", "account", "show", *(["--subscription", args.subscription] if args.subscription else []),
             "--only-show-errors", "--output", "json"]
        )  # fmt: skip
    except CommandError as error:
        raise Abort(
            f"The Azure CLI is not signed in, or the subscription was not found: {_first_lines(str(error), 2)}"
        ) from None
    if args.tenant and account.get("tenantId") != args.tenant:
        raise Abort(
            f"You are signed in to tenant {account.get('tenantId')}, but the connection is for tenant {args.tenant}. "
            f"Run az login --tenant {args.tenant} first."
        )
    return Azure(runner, account["id"]), account


def _check_federation(args: argparse.Namespace) -> None:
    if not args.issuer.startswith("https://") or not args.subject.strip():
        raise Abort("--issuer must be CloudBridge's https issuer URL and --subject its federated subject.")


def prefill_link(base: str | None, provider: str, values: dict[str, str]) -> str | None:
    if not base:
        return None
    query = urllib.parse.urlencode({"provider": provider, **{k: v for k, v in values.items() if v}})
    return f"{base.rstrip('/')}/providers/new?{query}"


def azure_plan(args: argparse.Namespace, runner: Runner) -> Plan:
    _check_federation(args)
    azure, account = azure_session(args, runner)
    group, env_name, location = args.resource_group, args.environment, args.location
    group_id = f"/subscriptions/{azure.subscription}/resourceGroups/{group}"
    app = AppRegistration(runner, azure, args.client_id, args.app_name)

    plan = Plan(
        [
            "CloudBridge setup for Azure Container Apps",
            f"  Signed in as {(account.get('user') or {}).get('name', 'unknown')}",
            f"  Subscription {account.get('name')} ({azure.subscription}), tenant {account.get('tenantId')}",
            f"  Resource group {group}, environment {env_name}, region {location}",
        ]
    )

    namespaces = ["Microsoft.App", "Microsoft.OperationalInsights", "Microsoft.ManagedIdentity"]
    if args.registry:
        namespaces.append("Microsoft.ContainerRegistry")
    todo = [
        n
        for n in namespaces
        if (runner.read_json(azure.sub("provider", "show", "--namespace", n)) or {}).get("registrationState")
        != "Registered"
    ]

    def register() -> str:
        for namespace in todo:
            runner.change(azure.sub("provider", "register", "--namespace", namespace, "--wait"))
        return f"Registered {', '.join(todo)}."

    plan.add(
        Step(
            "Turn on the Azure services Container Apps uses",
            [f"Registers the resource providers {', '.join(todo or namespaces)} in the subscription (free)."],
            register,
            done="they are registered" if not todo else None,
        )
    )

    env_type = "Microsoft.App/managedEnvironments"
    found_group = azure.show("group", "show", "--name", group)
    # What exists now; steps fill these in as they create things, so later steps know without asking Azure again.
    state: dict[str, Any] = {
        "group": found_group is not None,
        "env": azure.show(
            "resource", "show", "--resource-group", group, "--resource-type", env_type, "--name", env_name
        )
        if found_group
        else None,
        "registry": azure.show("acr", "show", "--name", args.registry) if args.registry else None,
        "identity": azure.show("identity", "show", "--name", RUNTIME_IDENTITY, "--resource-group", group)
        if found_group
        else None,
    }

    def need_group() -> str | None:
        return None if state["group"] else f"resource group {group} does not exist yet"

    def create_group() -> str:
        runner.change(azure.sub("group", "create", "--name", group, "--location", location, "--tags", MANAGED_TAG))
        state["group"] = True
        return f"Created resource group {group}."

    plan.add(
        Step(
            f"Create resource group {group}",
            [f"In {location}. CloudBridge creates container apps only in this group."],
            create_group,
            done="it exists" if state["group"] else None,
        )
    )

    try:
        runner.read(["az", "extension", "show", "--name", "containerapp", "--only-show-errors", "--output", "json"])
        has_extension = True
    except CommandError:
        has_extension = False

    def create_env() -> str:
        if not has_extension:
            runner.change(["az", "extension", "add", "--name", "containerapp", "--upgrade", "--only-show-errors"])
        runner.change(
            azure.sub(
                "containerapp", "env", "create", "--name", env_name, "--resource-group", group, "--location", location
            )
        )
        state["env"] = runner.read_json(
            azure.sub("resource", "show", "--resource-group", group, "--resource-type", env_type, "--name", env_name)
        )
        return f"Created environment {env_name}."

    plan.add(
        Step(
            f"Create Container Apps environment {env_name}",
            [
                "A Consumption environment with a new Log Analytics workspace for its logs.",
                "No charge while nothing runs; Log Analytics charges for the logs it stores.",
                "Takes a few minutes."
                + ("" if has_extension else " Installs the Azure CLI containerapp extension first."),
            ],
            create_env,
            done="it exists" if state["env"] else None,
            blocked=need_group,
        )
    )

    if args.registry:

        def create_registry() -> str:
            state["registry"] = runner.change_json(
                azure.sub(
                    "acr", "create", "--name", args.registry, "--resource-group", group, "--location", location,
                    "--sku", "Basic", "--tags", MANAGED_TAG,
                )
            )  # fmt: skip
            return f"Created registry {args.registry}.azurecr.io."

        plan.add(
            Step(
                f"Create container registry {args.registry}",
                ["Azure Container Registry, Basic tier (about US$5 a month). It holds the images CloudBridge builds."],
                create_registry,
                optional=True,
                done="it exists" if state["registry"] else None,
                blocked=need_group,
            )
        )

    app.add_steps(plan, args.issuer, args.subject, args.audience)

    def grant_group() -> str:
        return " ".join(
            azure.assign(str(app.principal_id), role, group_id) for role in ("Reader", "Container Apps Contributor")
        )

    plan.add(
        Step(
            f"Let CloudBridge manage container apps in {group}",
            [f"Gives the {app.label()} Reader and Container Apps Contributor on resource group {group} only."],
            grant_group,
            blocked=lambda: app.need() or need_group(),
        )
    )

    def grant_logs() -> str:
        properties = (state["env"] or {}).get("properties") or {}
        logs = (properties.get("appLogsConfiguration") or {}).get("logAnalyticsConfiguration") or {}
        customer = logs.get("customerId")
        scope = None
        if customer:
            workspaces = runner.read_json(azure.sub("monitor", "log-analytics", "workspace", "list")) or []
            scope = next((str(w["id"]) for w in workspaces if w.get("customerId") == customer), None)
        if not scope:
            raise CommandError([], 1, "The environment's Log Analytics workspace was not found in this subscription.")
        return azure.assign(str(app.principal_id), "Log Analytics Reader", scope)

    plan.add(
        Step(
            "Let CloudBridge read your application logs",
            [f"Gives the {app.label()} Log Analytics Reader on the environment's workspace only."],
            grant_logs,
            optional=True,
            blocked=lambda: app.need() or (None if state["env"] else f"environment {env_name} does not exist yet"),
        )
    )

    if args.registry:

        def grant_registry() -> str:
            scope = str(state["registry"]["id"])
            roles = ("AcrPull", "Container Registry Tasks Contributor")
            return " ".join(azure.assign(str(app.principal_id), role, scope) for role in roles)

        plan.add(
            Step(
                f"Let CloudBridge use registry {args.registry}",
                [
                    f"Gives the {app.label()} AcrPull and Container Registry Tasks Contributor on that registry only.",
                    "Needed to build from Git repositories and to deploy private images.",
                ],
                grant_registry,
                optional=True,
                blocked=lambda: (
                    app.need() or (None if state["registry"] else f"registry {args.registry} does not exist yet")
                ),
            )
        )

    def runtime_identity() -> str:
        if not state["identity"]:
            state["identity"] = runner.change_json(
                azure.sub(
                    "identity", "create", "--name", RUNTIME_IDENTITY, "--resource-group", group, "--location", location,
                    "--tags", MANAGED_TAG,
                )
            )  # fmt: skip
        identity = state["identity"]
        results = [azure.assign(str(app.principal_id), "Managed Identity Operator", str(identity["id"]))]
        if state["registry"]:
            pulled = azure.assign(str(identity["principalId"]), "AcrPull", str(state["registry"]["id"]))
            results.append(f"Runtime identity: {pulled}")
        return " ".join(results)

    plan.add(
        Step(
            f"{'Set up' if state['identity'] else 'Create'} the runtime identity {RUNTIME_IDENTITY}",
            [
                f"A user-assigned managed identity in {group} that your apps run as, for private images and Key Vault.",
                "CloudBridge may attach it to apps (Managed Identity Operator on this identity only)."
                + (f" It may pull from {args.registry} (AcrPull)." if args.registry else ""),
                "To use Key Vault secrets, give it Key Vault Secrets User on those vaults yourself.",
            ],
            runtime_identity,
            optional=True,
            blocked=lambda: app.need() or need_group(),
        )
    )

    def finish(_results: dict[str, str]) -> list[str]:
        if args.client_id:
            return ["All set. In CloudBridge, open the connection and select Validate connection."]
        values = {
            "tenant_id": str(account.get("tenantId") or ""),
            "subscription_id": azure.subscription,
            "client_id": app.client_id or "",
            "resource_group": group,
            "environment_name": env_name,
            "registry_name": args.registry or "",
            "default_region": location,
        }
        return hand_over("azure_container_apps", args.cloudbridge_url, values)

    plan.finish = finish
    return plan


LABELS = {
    "tenant_id": "Tenant ID",
    "subscription_id": "Subscription ID",
    "client_id": "Application (client) ID",
    "resource_group": "Resource group",
    "environment_name": "Container Apps environment",
    "registry_name": "Container registry",
    "default_region": "Default region",
    "api_server": "API server",
    "namespace": "Namespace",
    "ca_certificate": "Cluster CA certificate",
}


def hand_over(provider: str, cloudbridge_url: str | None, values: dict[str, str]) -> list[str]:
    """The values to enter in CloudBridge's form, and a link that fills them in."""
    lines = ["All set. Enter these values in CloudBridge (Cloud providers > Connect provider):"]
    for key, value in values.items():
        if not value:
            continue
        if "\n" in value:
            lines.append(f"  {LABELS[key]}:")
            lines += ["    " + line for line in value.splitlines()]
        else:
            lines.append(f"  {LABELS[key]}: {value}")
    link = prefill_link(cloudbridge_url, provider, values)
    if link:
        lines += ["", "Or open this link to start the connection with these values filled in:", f"  {link}"]
    return lines


# ---------------------------------------------------------------------------- AKS


def ca_from_kubeconfig(text: str) -> str | None:
    """The cluster's public CA certificate. Nothing else from the kubeconfig is kept or shown."""
    match = re.search(r"certificate-authority-data:\s*['\"]?([A-Za-z0-9+/=]+)", text)
    if not match:
        return None
    try:
        pem = base64.b64decode(match.group(1), validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return None
    return pem.strip() if "-----BEGIN CERTIFICATE-----" in pem else None


def aks_plan(args: argparse.Namespace, runner: Runner) -> Plan:
    _check_federation(args)
    azure, account = azure_session(args, runner)
    cluster = _find_cluster(args, runner, azure)
    cluster_id, name, group = cluster["id"], cluster["name"], cluster["resourceGroup"]
    aad = cluster.get("aadProfile") or {}
    if not aad.get("enableAzureRbac"):
        raise Abort(
            f"Cluster {name} does not use Microsoft Entra ID with Azure RBAC, which CloudBridge needs. Turn it on with "
            f"az aks update --resource-group {group} --name {name} --enable-aad --enable-azure-rbac "
            "(it changes how everyone signs in to the cluster), then run this script again."
        )
    fqdn = cluster.get("fqdn")
    if not fqdn:
        raise Abort(f"Cluster {name} has no public API server address, so CloudBridge cannot reach it.")
    api_server = args.api_server or f"https://{fqdn}:443"
    namespace = args.namespace
    app = AppRegistration(runner, azure, args.client_id, args.app_name)
    plan = Plan(
        [
            "CloudBridge setup for Azure Kubernetes Service",
            f"  Signed in as {(account.get('user') or {}).get('name', 'unknown')}",
            f"  Cluster {name} in {group}, namespace {namespace}",
        ]
    )

    def create_namespace() -> str:
        result = runner.change_json(
            azure.sub(
                "aks", "command", "invoke", "--resource-group", group, "--name", name,
                "--command", f"kubectl create namespace {namespace}",
            )
        ) or {}  # fmt: skip
        logs = str(result.get("logs") or "")
        if result.get("exitCode") not in (0, None) and "AlreadyExists" not in logs:
            raise CommandError([], int(result.get("exitCode") or 1), logs)
        return "It already existed." if "AlreadyExists" in logs else f"Created namespace {namespace}."

    plan.add(
        Step(
            f"Create namespace {namespace} if it does not exist",
            [
                "Runs kubectl create namespace inside the cluster as you (az aks command invoke, about a minute).",
                "Needs a cluster admin role. Skip this if the namespace already exists.",
            ],
            create_namespace,
            optional=True,
        )
    )
    app.add_steps(plan, args.issuer, args.subject, args.audience)
    scope = f"{cluster_id}/namespaces/{namespace}"

    plan.add(
        Step(
            f"Let CloudBridge deploy to namespace {namespace}",
            [f"Gives the {app.label()} Azure Kubernetes Service RBAC Writer on namespace {namespace} only."],
            lambda: azure.assign(str(app.principal_id), "Azure Kubernetes Service RBAC Writer", scope),
            blocked=app.need,
        )
    )

    def finish(_results: dict[str, str]) -> list[str]:
        if args.client_id:
            return ["All set. In CloudBridge, open the connection and select Validate connection."]
        try:
            kubeconfig = runner.read(
                azure.sub("aks", "get-credentials", "--resource-group", group, "--name", name, "--file", "-")
            )
            ca = ca_from_kubeconfig(kubeconfig)
        except CommandError:
            ca = None
        values = {
            "api_server": api_server,
            "namespace": namespace,
            "tenant_id": str(account.get("tenantId") or ""),
            "client_id": app.client_id or "",
            "default_region": "in-cluster",  # CloudBridge's only region for Kubernetes connections
            "ca_certificate": ca or "",
        }
        lines = hand_over("kubernetes", args.cloudbridge_url, values)
        if not ca:
            lines.append(
                f"  Cluster CA certificate: decode certificate-authority-data from "
                f"az aks get-credentials --resource-group {group} --name {name} --file -"
            )
        return lines

    plan.finish = finish
    return plan


def _find_cluster(args: argparse.Namespace, runner: Runner, azure: Azure) -> dict[str, Any]:
    if args.cluster and args.cluster_resource_group:
        found = azure.show("aks", "show", "--resource-group", args.cluster_resource_group, "--name", args.cluster)
        if not found:
            raise Abort(f"Cluster {args.cluster} was not found in resource group {args.cluster_resource_group}.")
        return found
    if not args.api_server:
        raise Abort("Say which cluster: --cluster and --cluster-resource-group, or --api-server.")
    host = urllib.parse.urlparse(args.api_server).hostname or ""
    for cluster in runner.read_json(azure.sub("aks", "list")) or []:
        if host and host in (cluster.get("fqdn"), cluster.get("privateFqdn")):
            return dict(cluster)
    raise Abort(f"No AKS cluster with API server {args.api_server} was found in subscription {azure.subscription}.")


# ---------------------------------------------------------------------------- other Kubernetes (OIDC)


def kubernetes_role(namespace: str) -> dict[str, Any]:
    """Only what CloudBridge does in the namespace. Keep in step with backend/app/providers/kubernetes.py."""
    write = ["get", "list", "create", "update", "patch", "delete"]
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {
            "name": "cloudbridge",
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "cloudbridge"},
        },
        "rules": [
            {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": write},
            {"apiGroups": ["apps"], "resources": ["deployments/scale"], "verbs": ["get", "update", "patch"]},
            {"apiGroups": [""], "resources": ["services"], "verbs": write},
            {"apiGroups": ["autoscaling"], "resources": ["horizontalpodautoscalers"], "verbs": write},
            {"apiGroups": [""], "resources": ["pods", "pods/log"], "verbs": ["get", "list"]},
            {"apiGroups": ["metrics.k8s.io"], "resources": ["pods"], "verbs": ["get", "list"]},
        ],
    }


def kubernetes_binding(namespace: str, subject: str) -> dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {
            "name": "cloudbridge",
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "cloudbridge"},
        },
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "cloudbridge"},
        "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": subject}],
    }


def kubernetes_plan(args: argparse.Namespace, runner: Runner) -> Plan:
    runner.tool("kubectl", "Install kubectl and point it at the cluster first.")
    if not args.issuer.startswith("https://") or not args.subject.strip() or not args.audience.strip():
        raise Abort("--issuer, --subject and --audience are required (CloudBridge shows them on the connection page).")
    base = ["kubectl", *(["--context", args.context] if args.context else [])]
    try:
        context = args.context or runner.read(["kubectl", "config", "current-context"]).strip()
    except CommandError as error:
        raise Abort(f"kubectl has no current cluster: {_first_lines(str(error), 2)}") from None
    namespace = args.namespace
    try:
        runner.read([*base, "get", "namespace", namespace, "--output", "json"])
        has_namespace = True
    except CommandError as error:
        if not missing(error, "NotFound", "not found"):
            raise Abort(f"kubectl could not reach the cluster: {_first_lines(str(error), 2)}") from None
        has_namespace = False

    plan = Plan(
        [
            "CloudBridge setup for a Kubernetes cluster (OIDC)",
            f"  kubectl context {context}, namespace {namespace}",
            f"  CloudBridge signs in as user {args.subject}",
        ]
    )

    def create_namespace() -> str:
        runner.change([*base, "create", "namespace", namespace])
        return f"Created namespace {namespace}."

    plan.add(
        Step(
            f"Create namespace {namespace}",
            ["CloudBridge works only in this namespace."],
            create_namespace,
            done="it exists" if has_namespace else None,
        )
    )
    role, binding = kubernetes_role(namespace), kubernetes_binding(namespace, args.subject)

    def apply_rbac() -> str:
        manifest = json.dumps({"apiVersion": "v1", "kind": "List", "items": [role, binding]})
        runner.change([*base, "apply", "-f", "-"], stdin=manifest)
        return "Applied Role and RoleBinding cloudbridge."

    plan.add(
        Step(
            f"Let CloudBridge deploy to namespace {namespace}",
            [f"Creates Role cloudbridge in {namespace} and binds user {args.subject} to it. The Role may:"]
            + [f"  {', '.join(r['verbs'])} {', '.join(r['resources'])}" for r in role["rules"]],
            apply_rbac,
        )
    )

    def finish(_results: dict[str, str]) -> list[str]:
        return [
            "Last, the API server must accept CloudBridge's token. Add this to its authentication configuration",
            "(--authentication-config; managed clusters call it an OIDC or JWT identity provider):",
            "",
            "  apiVersion: apiserver.config.k8s.io/v1beta1",
            "  kind: AuthenticationConfiguration",
            "  jwt:",
            "  - issuer:",
            f"      url: {args.issuer}",
            f"      audiences: [{json.dumps(args.audience)}]",
            "    claimMappings:",
            "      username:",
            "        claim: sub",
            '        prefix: ""',
            "",
            "Then, in CloudBridge, open the connection and select Validate connection.",
        ]

    plan.finish = finish
    return plan


# ---------------------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudbridge-connect.py",
        description="Set up a cloud account for CloudBridge, step by step, with your approval.",
    )
    sub = parser.add_subparsers(dest="provider", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dry-run", action="store_true", help="show every step without changing anything")

    aws = sub.add_parser("aws", help="AWS App Runner or ECS Express")
    aws.add_argument("--role-arn", required=True, help="the role CloudBridge assumes (created if it does not exist)")
    aws.add_argument("--principal", required=True, help="CloudBridge's own role ARN, which the role trusts")
    aws.add_argument("--external-id", required=True, help="the connection's external ID")
    aws.add_argument("--account", help="the connection's AWS account ID (checked against the role and your sign-in)")
    aws.add_argument("--ecs", action="store_true", help="set up ECS Express instead of App Runner")
    aws.add_argument("--ecr-prefix", default="cloudbridge", help="ECR repository prefix CloudBridge builds into")
    for key, default in AWS_DEFAULT_ROLES.items():
        aws.add_argument("--" + key.replace("_", "-"), dest=key, default=default, help=f"(default {default})")
    common(aws)

    def federation(p: argparse.ArgumentParser) -> None:
        p.add_argument("--issuer", required=True, help="CloudBridge's token issuer")
        p.add_argument("--subject", required=True, help="CloudBridge's token subject")
        p.add_argument("--audience", default=AZURE_AUDIENCE, help=f"(default {AZURE_AUDIENCE})")
        p.add_argument("--client-id", help="use this existing app registration instead of creating one")
        p.add_argument("--app-name", default="CloudBridge", help="name of a new app registration (default CloudBridge)")
        p.add_argument("--tenant", help="the connection's tenant ID (checked against your sign-in)")
        p.add_argument("--subscription", help="subscription ID or name (default: your current one)")
        p.add_argument("--cloudbridge-url", help="CloudBridge's address, to print a link that fills in the form")
        common(p)

    azure = sub.add_parser("azure", help="Azure Container Apps")
    azure.add_argument("--resource-group", required=True, help="created if it does not exist")
    azure.add_argument("--environment", required=True, help="Container Apps environment; created if it does not exist")
    azure.add_argument("--location", required=True, help="Azure region, e.g. eastus")
    azure.add_argument("--registry", help="Azure Container Registry name (optional; created if it does not exist)")
    federation(azure)

    aks = sub.add_parser("aks", help="Azure Kubernetes Service")
    aks.add_argument("--namespace", required=True, help="the namespace CloudBridge deploys to")
    aks.add_argument("--cluster", help="cluster name (with --cluster-resource-group)")
    aks.add_argument("--cluster-resource-group", help="the cluster's resource group")
    aks.add_argument("--api-server", help="or find the cluster by its API server address")
    federation(aks)

    k8s = sub.add_parser("kubernetes", help="any other cluster that can trust CloudBridge's OIDC token")
    k8s.add_argument("--namespace", required=True)
    k8s.add_argument("--issuer", required=True, help="CloudBridge's token issuer")
    k8s.add_argument("--subject", required=True, help="CloudBridge's token subject (its user name in the cluster)")
    k8s.add_argument("--audience", required=True, help="the connection's token audience")
    k8s.add_argument("--context", help="kubectl context (default: the current one)")
    common(k8s)
    return parser


PLANS = {"aws": aws_plan, "azure": azure_plan, "aks": aks_plan, "kubernetes": kubernetes_plan}


def main(argv: Sequence[str] | None = None, ask: Ask | None = None) -> int:
    args = build_parser().parse_args(argv)
    if ask is None:
        if not args.dry_run and not sys.stdin.isatty():
            print("This script asks before every change, so it needs an interactive terminal (or use --dry-run).")
            return 2
        ask = input
    runner = Runner(args.dry_run)
    try:
        plan = PLANS[args.provider](args, runner)
        return run_plan(plan, runner, ask)
    except Abort as error:
        print(f"Nothing was changed. {error}")
        return 2
    except CommandError as error:
        print(f"A command failed: {' '.join(error.args_list[:4])} ...\n  {_first_lines(str(error))}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Steps already done stay in place; run the script again to continue.")
        return 130
    finally:
        runner.close()


if __name__ == "__main__":
    sys.exit(main())
