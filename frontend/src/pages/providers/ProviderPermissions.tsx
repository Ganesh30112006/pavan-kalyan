import type { ProviderKind } from '../../types/api';

/**
 * What CloudBridge needs on each provider. Shown before connecting, so nothing is a surprise.
 * Keep in step with the calls in backend/app/providers/aws.py, gcp.py and azure.py.
 */
export function ProviderPermissions({ provider }: { provider: ProviderKind }) {
  if (provider === 'aws_app_runner') {
    return (
      <div className="stack text-small">
        <p>
          CloudBridge assumes an <strong>IAM role</strong> in your account with a unique external ID and receives short-lived
          credentials. You never create or paste access keys.
        </p>
        <ul className="permission-list">
          <li>
            <code>apprunner:CreateService</code>, <code>UpdateService</code>, <code>DescribeService</code>,{' '}
            <code>DeleteService</code>, <code>ListServices</code>, <code>ListOperations</code>, <code>TagResource</code>,{' '}
            <code>ListTagsForResource</code>, <code>CreateAutoScalingConfiguration</code>,{' '}
            <code>DescribeAutoScalingConfiguration</code>, <code>ListAutoScalingConfigurations</code>,{' '}
            <code>DeleteAutoScalingConfiguration</code> — manage the services it
            deploys
          </li>
          <li>
            <code>apprunner:AssociateCustomDomain</code>, <code>DescribeCustomDomains</code>,{' '}
            <code>DisassociateCustomDomain</code>, <code>PauseService</code>, <code>ResumeService</code> and{' '}
            <code>cloudwatch:GetMetricData</code> — custom domains, sleep schedules and metrics
          </li>
          <li>
            <code>ecr:DescribeRepositories</code>, <code>DescribeImages</code>, <code>CreateRepository</code>,{' '}
            <code>TagResource</code> — find images and create repositories for builds
          </li>
          <li>
            <code>codebuild:CreateProject</code>, <code>UpdateProject</code>, <code>StartBuild</code>, <code>StopBuild</code>,{' '}
            <code>BatchGetBuilds</code>, <code>ListProjects</code> — build images from repositories (repository sources only)
          </li>
          <li>
            <code>ecr:ListTagsForResource</code>, <code>DeleteRepository</code>, <code>codebuild:BatchGetProjects</code>,{' '}
            <code>DeleteProject</code> — remove the image repository and build project CloudBridge created when the
            application is deleted (optional; without them they are left in place)
          </li>
          <li>
            <code>logs:GetLogEvents</code>, <code>FilterLogEvents</code> — show build and runtime logs
          </li>
          <li>
            <code>iam:PassRole</code> on the <code>CloudBridgeAppRunnerECRAccess</code>, <code>CloudBridgeAppRunnerInstance</code>{' '}
            and <code>CloudBridgeCodeBuild</code> roles only
          </li>
          <li>
            <code>iam:CreateServiceLinkedRole</code> for <code>apprunner.amazonaws.com</code> only — App Runner creates its
            service-linked role when the first service in the account is created
          </li>
          <li>
            <code>secretsmanager:GetSecretValue</code> is granted to the <em>service's instance role</em> for referenced
            secrets, not to CloudBridge
          </li>
        </ul>
      </div>
    );
  }
  if (provider === 'aws_ecs_express') {
    return (
      <div className="stack text-small">
        <p>
          CloudBridge assumes an <strong>IAM role</strong> in your account with a unique external ID, exactly as for App Runner.
          You never create or paste access keys.
        </p>
        <ul className="permission-list">
          <li>
            <code>ecs:CreateExpressGatewayService</code>, <code>UpdateExpressGatewayService</code>,{' '}
            <code>DescribeExpressGatewayService</code>, <code>DeleteExpressGatewayService</code>,{' '}
            <code>DescribeServiceDeployments</code>, <code>DescribeServices</code>, <code>UpdateService</code>,{' '}
            <code>ListServices</code>, <code>ListClusters</code>, <code>DescribeClusters</code>, <code>TagResource</code>,{' '}
            <code>ListTagsForResource</code> — manage the services it deploys
          </li>
          <li>
            <code>ecs:CreateCluster</code>, <code>RegisterTaskDefinition</code>, <code>DeregisterTaskDefinition</code>,{' '}
            <code>DescribeTaskDefinition</code>, <code>CreateService</code>, <code>DeleteService</code>,{' '}
            <code>ListServiceDeployments</code>, <code>UntagResource</code> — Express Mode performs these as you (it creates
            the <code>default</code> cluster on the first deployment when no cluster is set)
          </li>
          <li>
            <code>iam:CreateServiceLinkedRole</code> for <code>ecs.amazonaws.com</code> and{' '}
            <code>elasticloadbalancing.amazonaws.com</code> — first service in an account only
          </li>
          <li>
            <code>ecr:DescribeRepositories</code>, <code>DescribeImages</code>, <code>CreateRepository</code> and the CodeBuild
            actions listed for App Runner — images and repository builds
          </li>
          <li>
            <code>logs:FilterLogEvents</code>, <code>GetLogEvents</code> and <code>cloudwatch:GetMetricData</code> — logs and
            metrics
          </li>
          <li>
            <code>iam:PassRole</code> on <code>CloudBridgeECSExecution</code>, <code>CloudBridgeECSInfrastructure</code> and{' '}
            <code>CloudBridgeCodeBuild</code> only
          </li>
          <li>
            The <em>execution role</em> (trusted by <code>ecs-tasks.amazonaws.com</code>, with{' '}
            <code>AmazonECSTaskExecutionRolePolicy</code>) reads referenced secrets; the <em>infrastructure role</em> (trusted by{' '}
            <code>ecs.amazonaws.com</code>, with <code>AmazonECSInfrastructureRoleforExpressGatewayServices</code>) creates the load
            balancer
          </li>
        </ul>
      </div>
    );
  }
  if (provider === 'kubernetes') {
    return (
      <div className="stack text-small">
        <p>
          CloudBridge calls your cluster's API server with a <strong>short-lived token</strong>: a Microsoft Entra token for an AKS
          app registration, or CloudBridge's own signed OIDC token for clusters configured to trust it. No kubeconfig, service
          account token or client certificate is stored.
        </p>
        <ul className="permission-list">
          <li>
            In the connection's namespace: create, patch, get and delete <code>deployments</code>, <code>services</code> and{' '}
            <code>horizontalpodautoscalers</code>; patch <code>deployments/scale</code>; list <code>pods</code> and read{' '}
            <code>pods/log</code>
          </li>
          <li>
            AKS: assign the app registration <code>Azure Kubernetes Service RBAC Writer</code> scoped to the namespace (the cluster
            must use Microsoft Entra ID with Azure RBAC)
          </li>
          <li>
            Optional: read <code>pods.metrics.k8s.io</code> for CPU and memory charts
          </li>
          <li>Referenced secrets must already exist in the namespace (<code>k8s:SECRET/KEY</code>)</li>
        </ul>
      </div>
    );
  }
  if (provider === 'azure_container_apps') {
    return (
      <div className="stack text-small">
        <p>
          CloudBridge signs in to your <strong>app registration</strong> through a federated credential and receives
          short-lived tokens. You never create or paste client secrets or certificates.
        </p>
        <ul className="permission-list">
          <li>
            <code>Reader</code> and <code>Container Apps Contributor</code> on the resource group — create, update and delete
            the container apps it deploys, in your existing Container Apps environment
          </li>
          <li>
            <code>AcrPull</code> on the container registry — find images and pin them by digest
          </li>
          <li>
            <code>Container Registry Tasks Contributor</code> on the registry — build images with ACR Tasks (repository
            sources only)
          </li>
          <li>
            <code>Log Analytics Reader</code> on the environment's workspace — show runtime logs
          </li>
          <li>
            <code>Managed Identity Operator</code> on the <code>cloudbridge-runtime</code> identity only — attach it to apps
            that use private images or Key Vault secrets
          </li>
          <li>
            <code>AcrPull</code> and <code>Key Vault Secrets User</code> are granted to the <em>runtime identity</em>, not to
            CloudBridge
          </li>
        </ul>
      </div>
    );
  }
  return (
    <div className="stack text-small">
      <p>
        CloudBridge authenticates through <strong>workload identity federation</strong> and impersonates a service account
        with short-lived tokens. You never create or download service account keys.
      </p>
      <ul className="permission-list">
        <li>
          <code>roles/run.admin</code> — create, update and delete Cloud Run services and set who can invoke them
        </li>
        <li>
          <code>roles/artifactregistry.writer</code> — publish and pull images (plus <code>roles/artifactregistry.admin</code>{' '}
          once, or create a Docker repository named <code>cloudbridge</code> yourself)
        </li>
        <li>
          <code>roles/cloudbuild.builds.editor</code> — build images from repositories (repository sources only)
        </li>
        <li>
          <code>roles/logging.viewer</code> — show build and runtime logs
        </li>
        <li>
          <code>roles/iam.serviceAccountUser</code> on the runtime service account only
        </li>
        <li>
          <code>roles/secretmanager.secretAccessor</code> is granted to the <em>runtime service account</em> for referenced
          secrets, not to CloudBridge
        </li>
      </ul>
    </div>
  );
}
