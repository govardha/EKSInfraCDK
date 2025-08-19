from dataclasses import dataclass
from aws_cdk import Stack, aws_iam as iam, aws_ec2 as ec2
from constructs import Construct


@dataclass
class CodeBuildRoleStackProps:
    deployment_account_id: str
    project_tags: dict
    resource_prefix: str
    target_env: str
    tenant_id: str
    vpc: ec2.Vpc


class CodeBuildRoleStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        props: CodeBuildRoleStackProps,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, tags=props.project_tags, **kwargs)

        eks_sg = ec2.SecurityGroup(
            self,
            "eks-sg",
            vpc=props.vpc,
            security_group_name=f"{props.resource_prefix}-eks-sg",
        )

        eks_sg.add_ingress_rule(
            peer=eks_sg,
            connection=ec2.Port.tcp(8080),
            description="Allow traffic on port 8080",
        )

        eks_sg.add_ingress_rule(
            peer=eks_sg,
            connection=ec2.Port.tcp(443),
            description="Allow traffic on port 443",
        )

        # Create an IAM role for the CodeBuild that runs step actions
        codebuild_role = iam.Role(
            self,
            f"{props.resource_prefix}-codebuild-role",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("codebuild.amazonaws.com"),
                # Add trust relationship for the pipeline account's CodeBuild role
                iam.ArnPrincipal(
                    f"arn:aws:iam::{props.deployment_account_id}:role/{props.resource_prefix}-codebuild-role"
                ),
            ),
            role_name=f"{props.resource_prefix}-eksctl-codebuild-role",
        )

        codebuild_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "autoscaling:*",
                    "cloudformation:*",
                    "codebuild:*",
                    "ec2:*",
                    "ecr:*",
                    "eks:*",
                    "elasticloadbalancing:DescribeLoadBalancers",
                    "events:*",
                    "iam:*",
                    "s3:*",
                    "secretsmanager:*",
                    "sqs:*",
                    "ssm:*",
                    "sts:*",
                    "sts:AssumeRole",
                    "sts:GetCallerIdentity",
                    "logs:*",
                ],
                resources=["*"],
            )
        )

        # Create SSM resource ARN
        resource_arn = (
            f"arn:aws:ssm:{self.region}:{self.account}:parameter/"
            f"{props.tenant_id}/{props.target_env}/*"
        )

        # Create SSM role to read parameters from the deployment account
        iam.Role(
            self,
            "ssm-role",
            assumed_by=iam.AccountPrincipal(props.deployment_account_id),
            role_name=f"{props.resource_prefix}-ssm-role",
            inline_policies={
                "ssm-role-policy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["ssm:GetParameter"],
                            resources=[resource_arn],
                        )
                    ]
                )
            },
        )
