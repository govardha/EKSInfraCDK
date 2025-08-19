from dataclasses import dataclass

from aws_cdk import (
    CfnJson,
    Fn,
    Stack,
    aws_iam as iam,
    aws_ssm as ssm,
)
from constructs import Construct

from config.path_builder import PathBuilder


@dataclass
class ExDnsSaRoleStackProps:
    deployment_account_id: str
    path_builder: PathBuilder
    project_tags: dict
    resource_prefix: str
    target_account_id: str
    target_env: str
    tenant_id: str


class ExDnsSaRoleStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 props: ExDnsSaRoleStackProps, **kwargs) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Read OIDC issuer ID from SSM
        oidc_id_param = ssm.StringParameter.from_string_parameter_name(
            self, "OidcIdParameter",
            string_parameter_name=props.path_builder.get_ssm_path(
                "eks", "oidc-id")
        )
        oidc_id = oidc_id_param.string_value

        # OIDC provider ARN
        oidc_provider_arn = Fn.join(
            "",
            [
                "arn:aws:iam::",
                props.target_account_id,
                ":oidc-provider/oidc.eks.",
                self.region,
                ".amazonaws.com/id/",
                oidc_id,
            ],
        )

        # Helper function to create an IRSA role with the correct Condition
        def _create_irsa_role(
            logical_id: str,
            role_name: str,
            service_account_sub: str,
            description: str,
        ) -> iam.Role:
            """
            Creates an IAM role assumed by the EKS OIDC provider, restricted
            to specified 'service_account_sub' (system:serviceaccount:ns:sa).
            """
            # Create the role with a FederatedPrincipal to the cluster's OIDC
            role = iam.Role(
                self,
                logical_id,
                role_name=role_name,
                assumed_by=iam.FederatedPrincipal(
                    federated=oidc_provider_arn,
                    conditions={},  # We will override with CfnJson
                    assume_role_action="sts:AssumeRoleWithWebIdentity",
                ),
                description=description,
            )

            # Construct the JSON key for the Condition, e.g.
            #  "oidc.eks.<REGION>.amazonaws.com/id/<OIDC_ID>:sub"
            sub_claim_key = Fn.join(
                "",
                [
                    "oidc.eks.",
                    self.region,
                    ".amazonaws.com/id/",
                    oidc_id,
                    ":sub",
                ],
            )

            # We restrict the Condition so only this service account can assume
            # e.g. system:serviceaccount:<namespace>:<serviceaccount_name>
            condition_json = CfnJson(
                self,
                f"{logical_id}ConditionJson",
                value={
                    "StringEquals": {
                        sub_claim_key: service_account_sub,
                    }
                },
            )

            # Override the trust policy's Condition with the CfnJson
            cfn_role = role.node.default_child
            cfn_role.add_property_override(
                "AssumeRolePolicyDocument.Statement.0.Condition",
                condition_json,
            )

            return role

        # External DNS IRSA Role
        external_dns_iam_role = _create_irsa_role(
            logical_id="ExternalDnsIamRole",
            role_name=f"{props.resource_prefix}-externaldns-sa-role",
            service_account_sub="system:serviceaccount:external-dns:external-dns",
            description="Allows ExternalDNS in tenant account to manage DNS records",
        )

        # External DNS role ARN in the deployment account
        external_dns_role_arn = (
            f"arn:aws:iam::{props.deployment_account_id}:role/"
            f"{props.resource_prefix}-externaldns-role"
        )
        external_dns_iam_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[external_dns_role_arn],
                effect=iam.Effect.ALLOW,
            )
        )

        # External Secrets Operator IRSA Role
        external_secrets_iam_role = _create_irsa_role(
            logical_id="ExternalSecretsIamRole",
            role_name=f"{props.resource_prefix}-secrets-manager-sa-role",
            service_account_sub=(
                "system:serviceaccount:kube-system:external-secrets-operator"
            ),
            description=(
                "Allows External Secrets Operator to pull secrets from AWS SM"
            ),
        )

        # External Secrets Operator IRSA Role
        external_secrets_iam_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "secretsmanager:GetResourcePolicy",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:ListSecretVersionIds",
                ],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:/{props.tenant_id}/{props.target_env}/*",
                ],
                effect=iam.Effect.ALLOW
            )
        )

        external_secrets_iam_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ssm:GetParameter",
                ],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/{props.tenant_id}/{props.target_env}/*"
                ],
                effect=iam.Effect.ALLOW
            )
        )

        # S3 IAM Role for Mapserver
        mapserver_iam_role = _create_irsa_role(
            logical_id="MapserverIamRole",
            role_name=f"{props.resource_prefix}-map-service-sa-role",
            description="Allows map-service in tenant account to manage S3 bucket",
        )

        mapserver_iam_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:PutObject"
                ],
                resources=[

                ],
                effect=iam.Effect.ALLOW
            )
        )

        