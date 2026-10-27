"""The guided setup script customers run in their own cloud shell (frontend/public/cloudbridge-connect.py).

The cloud command-line tools are replaced by a fake that records every command, so these tests check exactly
what the script would run, that nothing changes without approval, and that the permissions it grants cover
every call CloudBridge makes.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import botocore.session
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "frontend" / "public" / "cloudbridge-connect.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cloudbridge_connect", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load()

PRINCIPAL = "arn:aws:iam::999999999999:role/cloudbridge-api"
EXTERNAL_ID = "cb-0123456789abcdef0123456789abcdef"
ROLE_ARN = "arn:aws:iam::123456789012:role/CloudBridgeDeploy"
ISSUER = "https://abc123.tokens.sts.global.api.aws"
SUBJECT = PRINCIPAL
TENANT = "11111111-1111-1111-1111-111111111111"
SUBSCRIPTION = "22222222-2222-2222-2222-222222222222"


class Call:
    def __init__(self, args: list[str], stdin: str | None) -> None:
        self.args = args
        self.stdin = stdin
        # JSON documents passed as files are read now: the script deletes them when it finishes.
        self.docs: list[Any] = []
        for arg in args:
            path = arg[len("file://") :] if arg.startswith("file://") else arg[1:] if arg.startswith("@") else None
            if path:
                self.docs.append(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def key(self) -> str:
        words = []
        for arg in self.args:
            if arg.startswith("-"):
                break
            words.append(arg)
        return " ".join(words)

    def opt(self, name: str) -> str:
        return self.args[self.args.index(name) + 1]


Handler = Callable[[Call], Any]


class FakeCloud:
    """Answers commands by their leading words; anything unexpected fails the test."""

    def __init__(self, handlers: dict[str, Handler]) -> None:
        self.handlers = handlers
        self.calls: list[Call] = []

    def __call__(self, args: list[str], stdin: str | None) -> str:
        call = Call(list(args), stdin)
        self.calls.append(call)
        if call.key not in self.handlers:
            raise AssertionError(f"unexpected command: {' '.join(args)}")
        result = self.handlers[call.key](call)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, str) else json.dumps(result)

    def ran(self, key: str) -> list[Call]:
        return [c for c in self.calls if c.key == key]

    def changes(self) -> list[Call]:
        reads = {
            "show",
            "list",
            "get",
            "get-role",
            "get-policy",
            "get-caller-identity",
            "current-context",
            "get-credentials",
        }
        return [c for c in self.calls if not reads & set(c.key.split())]


def not_found(text: str = "NoSuchEntity: not found") -> Exception:
    error: Exception = cc.CommandError([], 254, text)
    return error


class Result:
    def __init__(self, code: int, lines: list[str], cloud: FakeCloud, prompts: list[str]) -> None:
        self.code = code
        self.output = "\n".join(lines)
        self.cloud = cloud
        self.prompts = prompts


def run(argv: list[str], cloud: FakeCloud, answers: list[str] | None = None) -> Result:
    """Runs the script against the fake cloud; answers are given to its questions in order (default: yes)."""
    lines: list[str] = []
    prompts: list[str] = []
    replies = list(answers) if answers is not None else None

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        if replies is None:
            return "y"
        if not replies:
            raise EOFError
        return replies.pop(0)

    args = cc.build_parser().parse_args(argv)
    runner = cc.Runner(args.dry_run, out=lines.append)
    runner.tool = lambda name, hint: None
    runner._exec = cloud
    try:
        plan = cc.PLANS[args.provider](args, runner)
        code = cc.run_plan(plan, runner, ask)
    except cc.Abort as error:
        lines.append(f"ABORT {error}")
        code = 2
    finally:
        runner.close()
    return Result(code, lines, cloud, prompts)


# ---------------------------------------------------------------------------- AWS


def aws_cloud(roles: dict[str, dict[str, Any]] | None = None, account: str = "123456789012") -> FakeCloud:
    existing = dict(roles or {})

    def get_role(call: Call) -> Any:
        name = call.opt("--role-name")
        return {"Role": existing[name]} if name in existing else not_found()

    def create_role(call: Call) -> Any:
        name = call.opt("--role-name")
        existing[name] = {"RoleName": name, "Path": call.opt("--path"), "Arn": f"arn:aws:iam::{account}:role/{name}"}
        return {"Role": existing[name]}

    def get_policy(call: Call) -> Any:
        return {} if "/service-role/" in call.opt("--policy-arn") else not_found()

    return FakeCloud(
        {
            "aws sts get-caller-identity": lambda c: {
                "Account": account,
                "Arn": f"arn:aws:sts::{account}:assumed-role/Admin/me",
            },
            "aws iam get-role": get_role,
            "aws iam create-role": create_role,
            "aws iam update-assume-role-policy": lambda c: "",
            "aws iam put-role-policy": lambda c: "",
            "aws iam get-policy": get_policy,
            "aws iam attach-role-policy": lambda c: "",
        }
    )


AWS_ARGS = [
    "aws",
    "--role-arn",
    ROLE_ARN,
    "--principal",
    PRINCIPAL,
    "--external-id",
    EXTERNAL_ID,
    "--account",
    "123456789012",
]


def inline_policy(cloud: FakeCloud, role: str) -> dict[str, Any]:
    calls = [c for c in cloud.ran("aws iam put-role-policy") if c.opt("--role-name") == role]
    assert len(calls) == 1, f"expected one policy for {role}"
    doc: dict[str, Any] = calls[0].docs[0]
    return doc


def allowed(policy: dict[str, Any]) -> set[str]:
    return {action for s in policy["Statement"] for action in s["Action"]}


def test_aws_dry_run_reads_but_changes_nothing() -> None:
    result = run([*AWS_ARGS, "--dry-run"], aws_cloud())

    assert result.code == 0
    assert result.cloud.changes() == []
    assert result.prompts == []
    assert "Create IAM role CloudBridgeDeploy for CloudBridge" in result.output
    assert "Dry run: not changed." in result.output


def test_aws_app_runner_creates_the_role_and_service_roles_after_approval() -> None:
    result = run(AWS_ARGS, aws_cloud())

    assert result.code == 0, result.output
    created = {c.opt("--role-name"): c for c in result.cloud.ran("aws iam create-role")}
    assert set(created) == {
        "CloudBridgeDeploy",
        "CloudBridgeAppRunnerECRAccess",
        "CloudBridgeAppRunnerInstance",
        "CloudBridgeCodeBuild",
    }
    trust = created["CloudBridgeDeploy"].docs[0]["Statement"][0]
    assert trust["Principal"] == {"AWS": PRINCIPAL}
    assert trust["Condition"] == {"StringEquals": {"sts:ExternalId": EXTERNAL_ID}}
    assert created["CloudBridgeAppRunnerECRAccess"].docs[0]["Statement"][0]["Principal"] == {
        "Service": "build.apprunner.amazonaws.com"
    }
    assert created["CloudBridgeAppRunnerInstance"].docs[0]["Statement"][0]["Principal"] == {
        "Service": "tasks.apprunner.amazonaws.com"
    }
    attached = result.cloud.ran("aws iam attach-role-policy")
    assert [(c.opt("--role-name"), c.opt("--policy-arn")) for c in attached] == [
        ("CloudBridgeAppRunnerECRAccess", "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess")
    ]
    # One question per step, and nothing that creates a long-lived credential.
    assert len(result.prompts) == 5
    assert not [c for c in result.cloud.calls if "access-key" in c.key or "login-profile" in c.key]


def test_aws_connection_policy_is_scoped() -> None:
    result = run(AWS_ARGS, aws_cloud())
    policy = inline_policy(result.cloud, "CloudBridgeDeploy")
    by_sid = {s["Sid"]: s for s in policy["Statement"]}

    assert by_sid["PassServiceRoles"]["Resource"] == [
        "arn:aws:iam::123456789012:role/CloudBridgeAppRunnerECRAccess",
        "arn:aws:iam::123456789012:role/CloudBridgeAppRunnerInstance",
        "arn:aws:iam::123456789012:role/CloudBridgeCodeBuild",
    ]
    assert by_sid["FirstServiceInAccount"]["Condition"] == {
        "StringEquals": {"iam:AWSServiceName": "apprunner.amazonaws.com"}
    }
    assert by_sid["BuildRepositories"]["Resource"] == "arn:aws:ecr:*:123456789012:repository/cloudbridge/*"
    assert by_sid["BuildProjects"]["Resource"] == "arn:aws:codebuild:*:123456789012:project/cloudbridge-*"
    assert not any(a.startswith("ecs:") for a in allowed(policy))
    build = inline_policy(result.cloud, "CloudBridgeCodeBuild")
    assert {s["Sid"]: s["Resource"] for s in build["Statement"]}["PushImages"] == (
        "arn:aws:ecr:*:123456789012:repository/cloudbridge/*"
    )


def _sdk_calls(source: str, services: tuple[str, ...]) -> list[set[str]]:
    """For every AWS SDK operation a provider module calls: the IAM actions that would allow it.

    An operation name several services share (list_tags_for_resource) is allowed by any of them."""
    session = botocore.session.get_session()
    called = set(re.findall(r"\.([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\(", source))
    models = {}
    for service in services:
        model = session.get_service_model(service)
        prefix = "cloudwatch" if service == "cloudwatch" else model.signing_name
        models[prefix] = {re.sub(r"(?<!^)(?=[A-Z])", "_", op).lower(): op for op in model.operation_names}
    needed = []
    for name in sorted(called):
        options = {f"{prefix}:{ops[name]}" for prefix, ops in models.items() if name in ops}
        if options:
            needed.append(options)
    return needed


@pytest.mark.parametrize("ecs", [False, True])
def test_aws_policy_allows_every_call_cloudbridge_makes(ecs: bool) -> None:
    providers = ROOT / "backend" / "app" / "providers"
    aws_source = (providers / "aws.py").read_text(encoding="utf-8")
    ecs_source = (providers / "ecs.py").read_text(encoding="utf-8")
    if ecs:
        needed = _sdk_calls(ecs_source, ("ecs", "logs", "cloudwatch")) + _sdk_calls(
            aws_source, ("ecr", "codebuild", "logs", "cloudwatch")
        )
    else:
        needed = _sdk_calls(aws_source, ("apprunner", "ecr", "codebuild", "logs", "cloudwatch"))
    assert len(needed) > 15  # the scan found the calls

    result = run([*AWS_ARGS, *(["--ecs"] if ecs else [])], aws_cloud())

    granted = allowed(inline_policy(result.cloud, "CloudBridgeDeploy"))
    assert [sorted(options) for options in needed if not options & granted] == []


def test_aws_ecs_sets_up_the_ecs_roles() -> None:
    result = run([*AWS_ARGS, "--ecs"], aws_cloud())

    assert result.code == 0, result.output
    created = {
        c.opt("--role-name"): c.docs[0]["Statement"][0]["Principal"] for c in result.cloud.ran("aws iam create-role")
    }
    assert created["CloudBridgeECSExecution"] == {"Service": "ecs-tasks.amazonaws.com"}
    assert created["CloudBridgeECSInfrastructure"] == {"Service": "ecs.amazonaws.com"}
    assert "CloudBridgeAppRunnerECRAccess" not in created
    policy = inline_policy(result.cloud, "CloudBridgeDeploy")
    linked = {s["Sid"]: s for s in policy["Statement"]}["FirstServiceInAccount"]["Condition"]
    assert linked == {
        "StringEquals": {"iam:AWSServiceName": ["ecs.amazonaws.com", "elasticloadbalancing.amazonaws.com"]}
    }
    assert not any(a.startswith("apprunner:") for a in allowed(policy))


def test_aws_uses_the_role_names_cloudbridge_is_configured_with() -> None:
    result = run([*AWS_ARGS, "--codebuild-role", "AcmeBuild", "--ecr-prefix", "acme"], aws_cloud())

    policy = inline_policy(result.cloud, "CloudBridgeDeploy")
    by_sid = {s["Sid"]: s for s in policy["Statement"]}
    assert "arn:aws:iam::123456789012:role/AcmeBuild" in by_sid["PassServiceRoles"]["Resource"]
    assert by_sid["BuildRepositories"]["Resource"] == "arn:aws:ecr:*:123456789012:repository/acme/*"
    assert "AcmeBuild" in {c.opt("--role-name") for c in result.cloud.ran("aws iam create-role")}


def test_aws_wrong_account_stops_before_anything_changes() -> None:
    result = run(AWS_ARGS, aws_cloud(account="555555555555"))

    assert result.code == 2
    assert "signed in to AWS account 555555555555" in result.output
    assert result.cloud.changes() == []


def test_aws_role_arn_must_match_the_account() -> None:
    args = [
        "aws",
        "--role-arn",
        ROLE_ARN,
        "--principal",
        PRINCIPAL,
        "--external-id",
        EXTERNAL_ID,
        "--account",
        "000000000000",
    ]
    result = run(args, aws_cloud())

    assert result.code == 2
    assert result.cloud.calls == []


def test_aws_existing_role_already_trusting_cloudbridge_is_left_alone() -> None:
    trust = cc.connection_trust(PRINCIPAL, EXTERNAL_ID)
    role = {"RoleName": "CloudBridgeDeploy", "Path": "/", "Arn": ROLE_ARN, "AssumeRolePolicyDocument": trust}
    result = run(AWS_ARGS, aws_cloud({"CloudBridgeDeploy": role}))

    assert result.code == 0, result.output
    assert result.cloud.ran("aws iam update-assume-role-policy") == []
    assert "Already done: its trust policy already allows only CloudBridge" in result.output
    assert "CloudBridgeDeploy" not in {c.opt("--role-name") for c in result.cloud.ran("aws iam create-role")}


def test_aws_existing_role_with_other_trust_is_replaced_only_after_warning() -> None:
    other = cc.connection_trust(PRINCIPAL, "cb-someone-else")
    role = {"RoleName": "CloudBridgeDeploy", "Path": "/", "Arn": ROLE_ARN, "AssumeRolePolicyDocument": other}
    result = run(AWS_ARGS, aws_cloud({"CloudBridgeDeploy": role}))

    assert "Anyone else it trusts today" in result.output
    [update] = result.cloud.ran("aws iam update-assume-role-policy")
    assert update.docs[0]["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == EXTERNAL_ID


def test_aws_role_with_same_name_on_another_path_is_refused() -> None:
    role = {
        "RoleName": "CloudBridgeDeploy",
        "Path": "/team/",
        "Arn": "arn:aws:iam::123456789012:role/team/CloudBridgeDeploy",
    }
    result = run(AWS_ARGS, aws_cloud({"CloudBridgeDeploy": role}))

    assert result.code == 2
    assert result.cloud.changes() == []


def test_aws_declined_step_blocks_the_steps_that_need_it() -> None:
    result = run(AWS_ARGS, aws_cloud(), answers=["n", "y", "y", "y"])

    assert result.code == 1
    assert "CloudBridge cannot connect until this is done" in result.output
    assert "Skipped: role CloudBridgeDeploy does not exist yet" in result.output
    assert "CloudBridgeDeploy" not in {c.opt("--role-name") for c in result.cloud.ran("aws iam put-role-policy")}
    assert "CloudBridgeDeploy" not in {c.opt("--role-name") for c in result.cloud.ran("aws iam create-role")}


def test_aws_quit_stops_immediately() -> None:
    result = run(AWS_ARGS, aws_cloud(), answers=["q"])

    assert result.code == 1
    assert result.cloud.changes() == []
    assert len(result.prompts) == 1


def test_aws_no_answer_counts_as_quit() -> None:
    result = run(AWS_ARGS, aws_cloud(), answers=[])

    assert result.code == 1
    assert result.cloud.changes() == []


def test_aws_failed_step_asks_before_going_on() -> None:
    cloud = aws_cloud()
    cloud.handlers["aws iam create-role"] = lambda c: cc.CommandError(
        [], 254, "AccessDenied: not allowed to iam:CreateRole"
    )
    result = run(AWS_ARGS, cloud, answers=["y", "n"])

    assert result.code == 1
    assert "Failed: AccessDenied: not allowed to iam:CreateRole" in result.output
    assert result.prompts[-1].strip().startswith("Continue with the next step?")
    assert len(result.cloud.ran("aws iam create-role")) == 1


# ---------------------------------------------------------------------------- Azure


def azure_cloud(*, existing_app: bool = False, trusted: bool = False, tenant: str = TENANT) -> FakeCloud:
    state: dict[str, Any] = {"group": False, "env": None, "acr": None, "identity": None}
    env = {
        "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/env",
        "properties": {"appLogsConfiguration": {"logAnalyticsConfiguration": {"customerId": "ws-customer"}}},
    }
    missing = cc.CommandError([], 3, "(ResourceNotFound) The Resource was not found.")

    def resource_show(call: Call) -> Any:
        return state["env"] or missing

    def env_create(call: Call) -> Any:
        state["env"] = env
        return {}

    def acr_create(call: Call) -> Any:
        state["acr"] = {
            "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.ContainerRegistry/registries/acme"
        }
        return state["acr"]

    def identity_create(call: Call) -> Any:
        state["identity"] = {"id": "/subscriptions/s/.../cloudbridge-runtime", "principalId": "identity-principal"}
        return state["identity"]

    credentials = [{"issuer": ISSUER, "subject": SUBJECT, "audiences": [cc.AZURE_AUDIENCE]}] if trusted else []
    return FakeCloud(
        {
            "az account show": lambda c: {
                "id": SUBSCRIPTION,
                "name": "Pay-As-You-Go",
                "tenantId": tenant,
                "user": {"name": "me@acme.test"},
            },
            "az provider show": lambda c: {
                "registrationState": "Registered" if c.opt("--namespace") == "Microsoft.App" else "NotRegistered"
            },
            "az provider register": lambda c: "",
            "az group show": lambda c: (
                {"id": "rg"}
                if state["group"]
                else cc.CommandError([], 3, "(ResourceGroupNotFound) Resource group 'rg' could not be found.")
            ),
            "az group create": lambda c: state.update(group=True) or {},
            "az resource show": resource_show,
            "az extension show": lambda c: {"name": "containerapp"},
            "az containerapp env create": env_create,
            "az acr show": lambda c: state["acr"] or missing,
            "az acr create": acr_create,
            "az identity show": lambda c: state["identity"] or missing,
            "az identity create": identity_create,
            "az ad app show": lambda c: {"appId": "client-1", "id": "app-object-1", "displayName": "Existing"},
            "az ad app create": lambda c: {"appId": "client-new", "id": "app-object-new"},
            "az ad sp show": lambda c: (
                {"id": "sp-1"} if existing_app else cc.CommandError([], 3, "Resource does not exist")
            ),
            "az ad sp create": lambda c: {"id": "sp-new"},
            "az ad app federated-credential list": lambda c: credentials,
            "az ad app federated-credential create": lambda c: {},
            "az role assignment create": lambda c: {},
            "az monitor log-analytics workspace list": lambda c: [
                {"id": "/ws/other", "customerId": "other"},
                {"id": "/ws/env-logs", "customerId": "ws-customer"},
            ],
        }
    )


AZURE_ARGS = [
    "azure", "--issuer", ISSUER, "--subject", SUBJECT, "--resource-group", "rg", "--environment", "env",
    "--location", "eastus", "--tenant", TENANT, "--cloudbridge-url", "https://cloudbridge.acme.test",
]  # fmt: skip


def assignments(cloud: FakeCloud) -> list[tuple[str, str, str]]:
    return [
        (c.opt("--assignee-object-id"), c.opt("--role"), c.opt("--scope"))
        for c in cloud.ran("az role assignment create")
    ]


def test_azure_sets_up_everything_and_prints_the_form_values() -> None:
    result = run([*AZURE_ARGS, "--registry", "acme"], azure_cloud())

    assert result.code == 0, result.output
    assert [c.opt("--namespace") for c in result.cloud.ran("az provider register")] == [
        "Microsoft.OperationalInsights",
        "Microsoft.ManagedIdentity",
        "Microsoft.ContainerRegistry",
    ]
    assert result.cloud.ran("az group create")[0].opt("--location") == "eastus"
    assert len(result.cloud.ran("az containerapp env create")) == 1
    [federated] = result.cloud.ran("az ad app federated-credential create")
    assert federated.opt("--id") == "app-object-new"
    assert federated.docs[0]["issuer"] == ISSUER
    assert federated.docs[0]["subject"] == SUBJECT
    assert federated.docs[0]["audiences"] == ["api://AzureADTokenExchange"]
    group = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg"
    registry = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.ContainerRegistry/registries/acme"
    assert assignments(result.cloud) == [
        ("sp-new", "Reader", group),
        ("sp-new", "Container Apps Contributor", group),
        ("sp-new", "Log Analytics Reader", "/ws/env-logs"),
        ("sp-new", "AcrPull", registry),
        ("sp-new", "Container Registry Tasks Contributor", registry),
        ("sp-new", "Managed Identity Operator", "/subscriptions/s/.../cloudbridge-runtime"),
        ("identity-principal", "AcrPull", registry),
    ]
    assert all(
        c.opt("--assignee-principal-type") == "ServicePrincipal" for c in result.cloud.ran("az role assignment create")
    )
    assert "Application (client) ID: client-new" in result.output
    assert (
        "https://cloudbridge.acme.test/providers/new?provider=azure_container_apps&tenant_id="
        f"{TENANT}&subscription_id={SUBSCRIPTION}&client_id=client-new&resource_group=rg&environment_name=env"
        "&registry_name=acme&default_region=eastus"
    ) in result.output


def test_azure_never_creates_a_secret() -> None:
    result = run([*AZURE_ARGS, "--registry", "acme"], azure_cloud())

    for call in result.cloud.calls:
        assert not ({"credential", "secret", "password"} & set(call.key.split())), call.key
        assert "--password" not in call.args and "--years" not in call.args


def test_azure_existing_app_already_trusting_cloudbridge() -> None:
    result = run([*AZURE_ARGS, "--client-id", "client-1"], azure_cloud(existing_app=True, trusted=True))

    assert result.code == 0, result.output
    assert result.cloud.ran("az ad app create") == []
    assert result.cloud.ran("az ad sp create") == []
    assert result.cloud.ran("az ad app federated-credential create") == []
    assert "Already done: it already trusts CloudBridge" in result.output
    assert {a[0] for a in assignments(result.cloud)} == {"sp-1"}
    assert "select Validate connection" in result.output
    assert "providers/new?" not in result.output


def test_azure_role_already_assigned_counts_as_done() -> None:
    cloud = azure_cloud(existing_app=True, trusted=True)
    cloud.handlers["az role assignment create"] = lambda c: cc.CommandError(
        [], 1, "(RoleAssignmentExists) The role assignment already exists."
    )
    result = run([*AZURE_ARGS, "--client-id", "client-1"], cloud)

    assert result.code == 0, result.output
    assert "Reader was already assigned." in result.output


def test_azure_wrong_tenant_stops_before_anything_changes() -> None:
    result = run(AZURE_ARGS, azure_cloud(tenant="33333333-3333-3333-3333-333333333333"))

    assert result.code == 2
    assert "az login --tenant" in result.output
    assert result.cloud.changes() == []


def test_azure_dry_run_changes_nothing() -> None:
    result = run([*AZURE_ARGS, "--registry", "acme", "--dry-run"], azure_cloud())

    assert result.code == 0
    assert result.cloud.changes() == []
    assert "Create app registration CloudBridge" in result.output


def test_azure_skipping_the_group_blocks_what_needs_it() -> None:
    # register: y, group: n, env (blocked), app: y, federate: y, grant group (blocked), logs (blocked), identity (blocked)
    result = run(AZURE_ARGS, azure_cloud(), answers=["y", "n", "y", "y"])

    assert result.cloud.ran("az containerapp env create") == []
    assert assignments(result.cloud) == []
    assert "Skipped: resource group rg does not exist yet" in result.output
    assert result.code == 1


# ---------------------------------------------------------------------------- AKS

CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
KUBECONFIG = f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {base64.b64encode(CA.encode()).decode()}
    server: https://aks-dns-1.hcp.eastus.azmk8s.io:443
  name: aks
users:
- name: clusterUser
  user:
    token: super-secret-token-value
"""


def aks_cloud(*, azure_rbac: bool = True) -> FakeCloud:
    cloud = azure_cloud()
    cluster = {
        "id": "/subscriptions/s/resourceGroups/k8s/providers/Microsoft.ContainerService/managedClusters/aks",
        "name": "aks",
        "resourceGroup": "k8s",
        "location": "eastus",
        "fqdn": "aks-dns-1.hcp.eastus.azmk8s.io",
        "aadProfile": {"managed": True, "enableAzureRbac": azure_rbac},
    }
    cloud.handlers["az aks list"] = lambda c: [{"fqdn": "other.example"}, cluster]
    cloud.handlers["az aks command invoke"] = lambda c: {"exitCode": 0, "logs": "namespace/apps created"}
    cloud.handlers["az aks get-credentials"] = lambda c: KUBECONFIG
    return cloud


AKS_ARGS = [
    "aks", "--issuer", ISSUER, "--subject", SUBJECT, "--namespace", "apps",
    "--api-server", "https://aks-dns-1.hcp.eastus.azmk8s.io:443", "--cloudbridge-url", "https://cb.test",
]  # fmt: skip


def test_aks_grants_namespace_access_and_prints_only_the_public_ca() -> None:
    result = run(AKS_ARGS, aks_cloud())

    assert result.code == 0, result.output
    [invoke] = result.cloud.ran("az aks command invoke")
    assert invoke.opt("--command") == "kubectl create namespace apps"
    assert assignments(result.cloud) == [
        (
            "sp-new",
            "Azure Kubernetes Service RBAC Writer",
            "/subscriptions/s/resourceGroups/k8s/providers/Microsoft.ContainerService/managedClusters/aks/namespaces/apps",
        )
    ]
    assert "API server: https://aks-dns-1.hcp.eastus.azmk8s.io:443" in result.output
    assert "    -----BEGIN CERTIFICATE-----" in result.output
    assert "super-secret-token-value" not in result.output
    assert "provider=kubernetes" in result.output and "ca_certificate=-----BEGIN" in result.output
    assert "default_region=in-cluster" in result.output


def test_aks_existing_namespace_is_fine() -> None:
    cloud = aks_cloud()
    cloud.handlers["az aks command invoke"] = lambda c: {"exitCode": 1, "logs": 'namespaces "apps" AlreadyExists'}
    result = run(AKS_ARGS, cloud)

    assert result.code == 0, result.output
    assert "It already existed." in result.output


def test_aks_without_azure_rbac_stops_and_explains() -> None:
    result = run(AKS_ARGS, aks_cloud(azure_rbac=False))

    assert result.code == 2
    assert "--enable-azure-rbac" in result.output
    assert result.cloud.changes() == []


def test_ca_is_read_from_the_kubeconfig_only_when_it_is_a_certificate() -> None:
    assert cc.ca_from_kubeconfig(KUBECONFIG) == CA
    assert cc.ca_from_kubeconfig("certificate-authority-data: bm90IGEgY2VydA==") is None
    assert cc.ca_from_kubeconfig("users: []") is None


# ---------------------------------------------------------------------------- other Kubernetes


def k8s_cloud(has_namespace: bool) -> FakeCloud:
    return FakeCloud(
        {
            "kubectl config current-context": lambda c: "prod\n",
            "kubectl get namespace apps": lambda c: (
                "{}"
                if has_namespace
                else cc.CommandError([], 1, 'Error from server (NotFound): namespaces "apps" not found')
            ),
            "kubectl create namespace apps": lambda c: "",
            "kubectl apply": lambda c: "",
        }
    )


K8S_ARGS = ["kubernetes", "--namespace", "apps", "--issuer", ISSUER, "--subject", SUBJECT, "--audience", "cloudbridge"]


def test_kubernetes_binds_cloudbridge_to_a_namespace_role() -> None:
    result = run(K8S_ARGS, k8s_cloud(has_namespace=False))

    assert result.code == 0, result.output
    assert len(result.cloud.ran("kubectl create namespace apps")) == 1
    [apply] = result.cloud.ran("kubectl apply")
    role, binding = json.loads(apply.stdin or "")["items"]
    rules = {
        (group, resource): set(r["verbs"])
        for r in role["rules"]
        for group in r["apiGroups"]
        for resource in r["resources"]
    }
    # What the backend's validation checks (kubernetes.py), plus what it reads.
    for key in (("apps", "deployments"), ("", "services"), ("autoscaling", "horizontalpodautoscalers")):
        assert {"create", "patch", "delete", "get"} <= rules[key]
    assert {"patch"} <= rules[("apps", "deployments/scale")]
    assert {"list"} <= rules[("", "pods")] and {"get"} <= rules[("", "pods/log")]
    assert role["metadata"]["namespace"] == "apps"
    assert binding["subjects"] == [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": SUBJECT}]
    assert f"url: {ISSUER}" in result.output and 'audiences: ["cloudbridge"]' in result.output


def test_kubernetes_existing_namespace_is_not_recreated() -> None:
    result = run(K8S_ARGS, k8s_cloud(has_namespace=True))

    assert result.cloud.ran("kubectl create namespace apps") == []
    assert len(result.prompts) == 1


# ---------------------------------------------------------------------------- command line


def test_main_refuses_to_change_anything_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert cc.main(AWS_ARGS) == 2
    assert "needs an interactive terminal" in capsys.readouterr().out


def test_script_runs_on_linux_as_served() -> None:
    # A carriage return after the shebang breaks ./cloudbridge-connect.py in CloudShell.
    content = SCRIPT.read_bytes()
    assert b"\r" not in content
    assert content.startswith(b"#!/usr/bin/env python3\n")


def test_policy_description_lists_every_statement() -> None:
    policy = cc.connection_policy("apprunner", "aws", "123456789012", dict(cc.AWS_DEFAULT_ROLES), "cloudbridge")
    lines = cc.describe_policy(policy)

    assert len(lines) == len(policy["Statement"])
    assert any("iam:PassRole on arn:aws:iam::123456789012:role/CloudBridgeAppRunnerECRAccess" in line for line in lines)
    assert any('when {"StringEquals":{"iam:AWSServiceName":"apprunner.amazonaws.com"}}' in line for line in lines)
