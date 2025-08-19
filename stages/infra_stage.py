from dataclasses import dataclass
from typing import Any, Dict

from aws_cdk import Stage
from constructs import Construct

from config.path_builder import PathBuilder
from stacks.acm_stack import AcmStack, AcmStackProps
from stacks.codebuild_role_stack import (CodeBuildRoleStack,
                                         CodeBuildRoleStackProps)
from stacks.vpc_import_stack import VpcImportStack, VpcImportStackProps


@dataclass
class InfraStageProps:
    app_infra_configs: Dict[str, Any]
    cluster_admin_role_name: str
    codebuild_role_arn: str
    deployment_account_id: str
    hosted_zone_name: str
    iam_identity_center_instance_arn: str
    karpenter_version: str
    kubernetes_version: str
    path_builder: PathBuilder
    products_purchased: list
    project_tags: dict
    resource_prefix: str
    target_env: str
    tenant_account_id: str
    tenant_id: str


class InfraStage(Stage):
    def __init__(self, scope: Construct, construct_id: str,
                 props: InfraStageProps, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Import VPC
        vpc_stack = VpcImportStack(
            self,
            f"{props.resource_prefix}-vpc-import",
            props=VpcImportStackProps(
                resource_prefix=props.resource_prefix,
                project_tags=props.project_tags,
            ),
        )

        CodeBuildRoleStack(
            self,
            f"{props.resource_prefix}-codebuild-role-stack",
            props=CodeBuildRoleStackProps(
                deployment_account_id=props.deployment_account_id,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
                target_env=props.target_env,
                tenant_id=props.tenant_id,
                vpc=vpc_stack.vpc,
            ),
        )

        AcmStack(
            self,
            f"{props.resource_prefix}-acm-stack",
            props=AcmStackProps(
                deployment_account_id=props.deployment_account_id,
                hosted_zone_name=props.hosted_zone_name,
                path_builder=props.path_builder,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
            ),
        )

        EfsStack(
            self,
            f"{props.resource_prefix}-efs-stack",
            props=EfsStackProps(
                path_builder=props.path_builder,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
                vpc=vpc_stack.vpc,
            ),
        )

        SecretStack(
            self,
            f"{props.resource_prefix}-secret-stack",
            props=SecretStackProps(
                path_builder=props.path_builder,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
            ),
        )
