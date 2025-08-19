#!/usr/bin/env python3

import aws_cdk as cdk
from aws_cdk import Environment

from config.config import DEPLOYMENT_ENV, load_configurations
from config.path_builder import PathBuilder
from pipelines.infra_pipeline import (InfraPipelineStack,
                                      InfraPipelineStackProps)
from stacks.toolchain_stack import ToolchainStack, ToolchainStackProps

app = cdk.App()

# Get the tenant from the CLI argument `tenant_id`
tenant_id = app.node.try_get_context("tenant_id")

# Get the environment from the CLI argument `target_env`
target_env = app.node.try_get_context("target_env")

# Load the ECR repositories configuration within ./config/ecr_repositories.yaml
ecr_repositories = load_configurations()["ecr_repositories"]

# Load the environments configuration within ./config/environments.yaml
environments = load_configurations()["environments"]

# Load the infra configuration within ./config/config.yaml
infra_config = load_configurations()["config"]

# Load the Deployment account configuration
deploy_config = infra_config["deployment"]

# Load the infrastructure configurations for each application
app_infra_configs = infra_config["applications"]

# Load the code connection ARN
code_connection_arn = deploy_config["code_connection_arn"]

# Load the branch name for deployment
deployment_branch_name = deploy_config["deployment_branch_name"]

# Load the repository name for deployment
github_owner = deploy_config["github_owner"]
github_repo = deploy_config["github_repo"]

# Load the kubernetes and karpenter versions
kubernetes_version = deploy_config["kubernetes_version"]
karpenter_version = deploy_config["karpenter_version"]

# Load the project tags
project_tags = deploy_config["project_tags"]

# Deploy the toolchain stack if no tenant_id is provided
if not tenant_id:
    ToolchainStack(
        app,
        "infra-toolchain",
        props=ToolchainStackProps(
            ecr_repositories=ecr_repositories,
            organization_id=infra_config["organization_id"],
            project_tags=project_tags,
        ),
        env=DEPLOYMENT_ENV,
    )

# Check if tenant_id and target_env are valid
elif tenant_id in environments and target_env in environments[tenant_id]:
    # Create string prefix for resources
    resource_prefix = f"{tenant_id}-{target_env}"

    # Get the application configurations for the tenant and target environment
    application_config = environments[tenant_id][target_env]["applications"]

    # Get the agency env config to deploy the infrastructure pipeline
    tenant_config = environments[tenant_id][target_env]

    # Load the target account environment
    target_account_env = Environment(
        account=tenant_config["account"],
        region=tenant_config["region"]
    )

    # Create a PathBuilder instance to centralize SSM param path construction.
    path_builder = PathBuilder(
        tenant_id=tenant_id,
        environment=target_env,
        config_file="config/ssm_paths.yaml"
    )

    # Create the InfraPipelineStack
    InfraPipelineStack(
        app,
        f"{resource_prefix}-infra-pipeline",
        props=InfraPipelineStackProps(
            app_infra_configs=app_infra_configs,
            application_config=application_config,
            cluster_admin_role_name=tenant_config["cluster_admin_role_name"],
            code_connection_arn=code_connection_arn,
            deployment_branch_name=deployment_branch_name,
            deployment_env=DEPLOYMENT_ENV,
            email_subscriptions=infra_config["email_subscriptions"],
            enable_manual_approval=tenant_config["enable_manual_approval"],
            github_owner=github_owner,
            github_repo=github_repo,
            hosted_zone_arn=deploy_config["hosted_zone_arn"],
            hosted_zone_name=deploy_config["hosted_zone_name"],
            iam_identity_center_instance_arn=infra_config["iam_identity_center_instance_arn"],
            karpenter_version=karpenter_version,
            kubernetes_version=kubernetes_version,
            path_builder=path_builder,
            products_purchased=tenant_config["products_purchased"],
            project_tags=project_tags,
            resource_prefix=resource_prefix,
            target_account=target_account_env,
            target_env=target_env,
            tenant_id=tenant_id,
        ),
        env=DEPLOYMENT_ENV,
    )

# If tenant_id and target_env are not valid, raise an error
else:
    valid_envs = [
        f"Tenant: {tenant}, Environment: {env}"
        for tenant, env_dict in environments.items()
        for env in env_dict.keys()
    ]
    raise ValueError(
        f'Invalid arguments: {tenant_id}, {target_env}. '
        f'Valid arguments are: {"; ".join(valid_envs)}'
    )

app.synth()
