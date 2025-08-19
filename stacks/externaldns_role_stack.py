from dataclasses import dataclass

from aws_cdk import (
    Stack,
    aws_iam as iam,
)
from constructs import Construct

from config.path_builder import PathBuilder


@dataclass
class ExternalDnsRoleStackProps:
    hosted_zone_arn: str
    path_builder: PathBuilder
    resource_prefix: str
    project_tags: dict
    target_account_id: str


class ExternalDnsRoleStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 props: ExternalDnsRoleStackProps, **kwargs,) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Create role that trusts the target account
        external_dns_iam_role = iam.Role(
            self, "ExternalDnsIamRole",
            role_name=f"{props.resource_prefix}-externaldns-role",
            assumed_by=iam.AccountPrincipal(props.target_account_id),
            description=(
                "Allows ExternalDNS in tenant account to manage DNS records"
            ),
        )

        # Attach Route53 permissions
        external_dns_iam_role.add_to_policy(
            iam.PolicyStatement(
                actions=["route53:ChangeResourceRecordSets"],
                resources=[props.hosted_zone_arn],
                effect=iam.Effect.ALLOW,
            )
        )

        external_dns_iam_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "route53:ListHostedZones",
                    "route53:ListResourceRecordSets",
                ],
                resources=["*"],
                effect=iam.Effect.ALLOW,
            )
        )
