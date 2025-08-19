from dataclasses import dataclass

from aws_cdk import Environment, Stage
from constructs import Construct

from config.path_builder import PathBuilder
from stacks.vpc_stack import VpcStack, VpcStackProps
from stacks.dns_role_stack import DnsRoleStack, DnsRoleStackProps


@dataclass
class NetworkStageProps:
    deployment_env: Environment
    hosted_zone_arn: str
    path_builder: PathBuilder
    resource_prefix: str
    target_account_id: str
    target_env: str
    tenant_id: str
    project_tags: dict


class NetworkStage(Stage):
    @property
    def vpc_stack(self):
        """Store the VPC stack as a property of the stage"""
        return self._vpc_stack

    def __init__(
        self, scope: Construct, construct_id: str, props: NetworkStageProps, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create the VPC Stack
        self._vpc_stack = VpcStack(
            self,
            f"{props.resource_prefix}-vpc",
            props=VpcStackProps(
                path_builder=props.path_builder,
                resource_prefix=props.resource_prefix,
                target_env=props.target_env,
                tenant_id=props.tenant_id,
                project_tags=props.project_tags,
            ),
        )

        # Create the DNS Role Stack
        DnsRoleStack(
            self,
            f"{props.resource_prefix}-dns-role",
            props=DnsRoleStackProps(
                hosted_zone_arn=props.hosted_zone_arn,
                path_builder=props.path_builder,
                resource_prefix=props.resource_prefix,
                target_account_id=props.target_account_id,
                project_tags=props.project_tags,
            ),
            env=props.deployment_env,
        )
