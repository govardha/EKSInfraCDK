from dataclasses import dataclass

from aws_cdk import (
    aws_iam as iam,
    Stack,
)
from constructs import Construct

from config.path_builder import PathBuilder


@dataclass
class DnsRoleStackProps:
    hosted_zone_arn: str
    path_builder: PathBuilder
    resource_prefix: str
    target_account_id: str
    project_tags: dict


class DnsRoleStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, props: DnsRoleStackProps, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, tags=props.project_tags, **kwargs)

        # Create customer-managed policy for changing Route 53 records
        change_rrsets_policy = iam.ManagedPolicy(
            self,
            "Route53ChangeResourceRecordSetsPolicy",
            managed_policy_name=(
                f"{props.resource_prefix}-route53-change-rrsets-policy"
            ),
            description=("Policy to allow changing resource record sets in Route 53"),
            statements=[
                iam.PolicyStatement(
                    actions=["route53:ChangeResourceRecordSets"],
                    resources=[props.hosted_zone_arn],
                    effect=iam.Effect.ALLOW,
                ),
            ],
        )

        # Create customer-managed policy for listing Route 53 hosted zones
        list_hosted_zones_policy = iam.ManagedPolicy(
            self,
            "Route53ListHostedZonesPolicy",
            managed_policy_name=(
                f"{props.resource_prefix}-route53-list-hosted-zones-policy"
            ),
            description=("Policy to allow listing hosted zones in Route 53"),
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "route53:ListHostedZones",
                        "route53:ListResourceRecordSets"
                    ],
                    resources=["*"],
                    effect=iam.Effect.ALLOW,
                ),
            ],
        )

        # Create customer-managed policy for SSM parameter operations
        ssm_get_parameters_policy = iam.ManagedPolicy(
            self,
            "SSMGetParametersPolicy",
            managed_policy_name=(f"{props.resource_prefix}-ssm-get-parameters-policy"),
            description=("Policy for getting, putting, and deleting parameters in SSM"),
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "ssm:GetParameter*",
                        "ssm:DeleteParameter*",
                        "ssm:PutParameter*",
                    ],
                    resources=[
                        f"arn:aws:ssm:{self.region}:" f"{self.account}:parameter/acm/*",
                    ],
                    effect=iam.Effect.ALLOW,
                ),
            ],
        )

        # Create the IAM Role for DNS validation
        dns_role = iam.Role(
            self,
            "dns-validation-role",
            role_name=f"{props.resource_prefix}-dns-validation-role",
            assumed_by=iam.AccountPrincipal(props.target_account_id),
            description="Role to allow validation of certificate",
        )

        # Attach the customer-managed policies to the role
        dns_role.add_managed_policy(change_rrsets_policy)
        dns_role.add_managed_policy(list_hosted_zones_policy)
        dns_role.add_managed_policy(ssm_get_parameters_policy)

        # Add the environment account to the trust policy of the role
        dns_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                principals=[iam.AccountPrincipal(props.target_account_id)],
            )
        )
