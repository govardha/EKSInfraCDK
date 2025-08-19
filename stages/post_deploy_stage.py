from dataclasses import dataclass

from aws_cdk import Environment, Stage
from constructs import Construct

from config.path_builder import PathBuilder
from stacks.externaldns_role_stack import (
    ExternalDnsRoleStack,
    ExternalDnsRoleStackProps,
)
from stacks.exdns_sa_role_stack import (
    ExDnsSaRoleStack,
    ExDnsSaRoleStackProps,
)


@dataclass
class PostDeployStageProps:
    deployment_env: Environment
    hosted_zone_arn: str
    path_builder: PathBuilder
    project_tags: dict
    resource_prefix: str
    target_account_id: str
    target_env: str
    tenant_id: str


class PostDeployStage(Stage):

    def __init__(self, scope: Construct, construct_id: str,
                 props: PostDeployStageProps, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create the ExternalDNS Service Account Role Stack
        ExDnsSaRoleStack(
            self, f"{props.resource_prefix}-exdns-sa-role-stack",
            props=ExDnsSaRoleStackProps(
                deployment_account_id=props.deployment_env.account,
                path_builder=props.path_builder,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
                target_account_id=props.target_account_id,
                target_env=props.target_env,
                tenant_id=props.tenant_id,
            ),
        )

        # Create the ExternalDNS Role Stack
        ExternalDnsRoleStack(
            self, f"{props.resource_prefix}-externaldns-role-stack",
            props=ExternalDnsRoleStackProps(
                hosted_zone_arn=props.hosted_zone_arn,
                path_builder=props.path_builder,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
                target_account_id=props.target_account_id,
            ),
            env=props.deployment_env,
        )
