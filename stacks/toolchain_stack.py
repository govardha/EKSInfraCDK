from dataclasses import dataclass

from aws_cdk import (
    Stack,
    aws_ecr as ecr,
    aws_iam as iam,
)
from constructs import Construct


@dataclass
class ToolchainStackProps:
    ecr_repositories: dict
    organization_id: str
    project_tags: dict


class ToolchainStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 props: ToolchainStackProps, **kwargs) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Iterate over the ECR repositories
        for namespace, repositories in props.ecr_repositories.items():
            for repo in repositories:
                # Create a unique id for the construct by combining namespace
                # and repo. This must be unique within the stack.
                construct_id_for_repo = f"{namespace}-{repo}"

                # The ECR repository name with namespace
                repository_name = f"{namespace}/{repo}"

                # Create the ECR repository
                repo = ecr.Repository(
                    self,
                    id=construct_id_for_repo,
                    repository_name=repository_name,
                    image_scan_on_push=True,
                    # image_tag_mutability=ecr.TagMutability.MUTABLE,
                    # removal_policy=RemovalPolicy.RETAIN,
                    # lifecycle_rules=[
                    #     ecr.LifecycleRule(
                    #         enabled=True,
                    #         expiration_days=30,
                    #         delete_after=Duration.days(30),
                    #     )
                    # ]
                )

                # Add the repository policy
                repo.add_to_resource_policy(
                    iam.PolicyStatement(
                        sid="organization-pull",
                        effect=iam.Effect.ALLOW,
                        principals=[iam.AnyPrincipal()],
                        actions=[
                            "ecr:BatchCheckLayerAvailability",
                            "ecr:BatchGetImage",
                            "ecr:DescribeImages",
                            "ecr:DescribeRepositories",
                            "ecr:GetDownloadUrlForLayer",
                            "ecr:ListImages",
                        ],
                        conditions={
                            "ForAnyValue:StringLike": {
                                "aws:PrincipalOrgPaths": f"{props.organization_id}/*"
                            }
                        },
                    )
                )
