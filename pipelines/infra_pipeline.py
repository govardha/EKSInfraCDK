from dataclasses import dataclass
from typing import Dict, Any

from aws_cdk import (
    Environment,
    Stack,
    pipelines,
    aws_iam as iam,
    aws_codebuild as codebuild,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions,
    aws_codestarnotifications as notifications
)
from constructs import Construct

from config.path_builder import PathBuilder
from stages.infra_stage import InfraStage, InfraStageProps
from stages.network_stage import NetworkStage, NetworkStageProps
from stages.post_deploy_stage import PostDeployStage, PostDeployStageProps


@dataclass
class InfraPipelineStackProps:
    app_infra_configs: Dict[str, Any]
    application_config: Dict[str, Any]
    cluster_admin_role_name: str
    code_connection_arn: str
    deployment_branch_name: str
    deployment_env: Environment
    email_subscriptions: list
    enable_manual_approval: bool
    github_owner: str
    github_repo: str
    hosted_zone_arn: str
    hosted_zone_name: str
    iam_identity_center_instance_arn: str
    karpenter_version: str
    kubernetes_version: str
    path_builder: PathBuilder
    products_purchased: list
    project_tags: dict
    resource_prefix: str
    target_account: Environment
    target_env: str
    tenant_id: str


class InfraPipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 props: InfraPipelineStackProps, **kwargs,) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Helper function to build IAM role ARN strings with resource prefix
        def build_role_arn(account, role_name):
            return (
                f"arn:aws:iam::{account}:role/"
                f"{props.resource_prefix}-{role_name}"
            )

        # Define the cluster access role ARN
        cluster_access_role_arn = build_role_arn(
            props.target_account.account, "eksctl-codebuild-role")

        # Define CodeBuild role for installing cluster add-ons
        codebuild_role = iam.Role(
            self,
            "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            role_name=f"{props.resource_prefix}-codebuild-role",
            inline_policies={
                "CodeBuildPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=[
                                "cloudformation:*",
                                "iam:*",
                                "kms:*",
                                "s3:*",
                                "secretsmanager:*",
                                "ssm:*",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            actions=["sts:AssumeRole"],
                            resources=[cluster_access_role_arn],
                        ),
                    ]
                )
            },
        )

        # Define IAM policy for pipeline to assume lookup role
        pipeline_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["sts:AssumeRole"],
            resources=["*"],
            conditions={
                "StringEquals": {
                    "iam:ResourceTag/aws-cdk:bootstrap-role": [
                        "lookup",
                        "deploy",
                        "file-publishing",
                        "image-publishing",
                    ],
                }
            },
        )

        # Create CodePipeline pipeline for infrastructure deployment
        pipeline = pipelines.CodePipeline(
            self,
            "infra-pipeline",
            pipeline_name=f"{props.resource_prefix}-infra-pipeline",
            self_mutation=True,
            cross_account_keys=True,
            docker_enabled_for_synth=True,
            synth=pipelines.CodeBuildStep(
                "Synth",
                input=pipelines.CodePipelineSource.connection(
                    f"{props.github_owner}/{props.github_repo}",
                    branch=props.deployment_branch_name,
                    connection_arn=props.code_connection_arn,
                ),
                commands=[
                    "npm install -g aws-cdk",
                    "pip install -r requirements.txt",
                    "cdk synth -c tenant_id=$tenant_id -c target_env=$target_env",
                ],
                env={
                    "tenant_id": props.tenant_id,
                    "target_env": props.target_env,
                },
                role_policy_statements=[pipeline_policy],
            ),
        )

        # Add Network Stage to Pipeline
        network_stage = NetworkStage(
            self,
            f"{props.resource_prefix}-network",
            props=NetworkStageProps(
                deployment_env=props.deployment_env,
                hosted_zone_arn=props.hosted_zone_arn,
                path_builder=props.path_builder,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
                target_account_id=props.target_account.account,
                target_env=props.target_env,
                tenant_id=props.tenant_id,
            ),
            env=props.target_account,
        )
        pipeline.add_stage(network_stage)

        # Add Infra Stage to Pipeline
        infra_stage = InfraStage(
            self,
            f"{props.resource_prefix}-infra",
            props=InfraStageProps(
                app_infra_configs=props.app_infra_configs,
                cluster_admin_role_name=props.cluster_admin_role_name,
                codebuild_role_arn=codebuild_role.role_arn,
                deployment_account_id=props.deployment_env.account,
                hosted_zone_name=props.hosted_zone_name,
                iam_identity_center_instance_arn=props.iam_identity_center_instance_arn,
                karpenter_version=props.karpenter_version,
                kubernetes_version=props.kubernetes_version,
                path_builder=props.path_builder,
                products_purchased=props.products_purchased,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
                target_env=props.target_env,
                tenant_account_id=props.target_account.account,
                tenant_id=props.tenant_id,
            ),
            env=props.target_account,
        )
        pipeline.add_stage(infra_stage)

        # Add wave for deploying EKS cluster
        deploy_eks_cluster = pipeline.add_wave("deploy-eks-cluster-wave")
        deploy_eks_cluster.add_post(
            pipelines.CodeBuildStep(
                "deploy-eks-cluster-step",
                project_name=f"{props.resource_prefix}-deploy-eks-cluster",
                build_environment=codebuild.BuildEnvironment(
                    build_image=codebuild.LinuxBuildImage.STANDARD_7_0
                ),
                commands=[
                    "cd scripts/cluster_config",
                    "chmod +x deploy_eks_cluster.sh",
                    "./deploy_eks_cluster.sh",
                ],
                env={
                    "AWS_REGION": props.target_account.region,
                    "CLUSTER_ACCESS_ROLE_ARN": cluster_access_role_arn,
                    "CLUSTER_ADMIN_ROLE_NAME": props.cluster_admin_role_name,
                    "CLUSTER_NAME": f"{props.resource_prefix}-cluster",
                    "CODE_BUILD_ROLE_NAME": f"{props.resource_prefix}-eksctl-codebuild-role",
                    "KARPENTER_VERSION": props.karpenter_version,
                    "KUBERNETES_VERSION": props.kubernetes_version,
                    "MAP_MIGRATED": props.project_tags["map-migrated"],
                    "OIDC_ID_PARAM": props.path_builder.get_ssm_path("eks", "oidc-id"),
                    "RESOURCE_PREFIX": props.resource_prefix,
                    "TARGET_ACCOUNT_ID": props.target_account.account,
                },
                role=codebuild_role,
            )
        )

        # Add Post Deploy Stage to Pipeline
        post_deploy_stage = PostDeployStage(
            self,
            f"{props.resource_prefix}-post-deploy",
            props=PostDeployStageProps(
                deployment_env=props.deployment_env,
                hosted_zone_arn=props.hosted_zone_arn,
                path_builder=props.path_builder,
                project_tags=props.project_tags,
                resource_prefix=props.resource_prefix,
                target_account_id=props.target_account.account,
                target_env=props.target_env,
                tenant_id=props.tenant_id,
            ),
            env=props.target_account,
        )
        pipeline.add_stage(post_deploy_stage)

        # Define the ExternalDNS role ARN
        external_dns_role_arn = build_role_arn(
            props.deployment_env.account, "externaldns-role")

        # Define the ExternalDNS service account role ARN
        external_dns_sa_role_arn = build_role_arn(
            props.target_account.account, "externaldns-sa-role")

        # Define the External Secrets Operator service account role ARN
        external_secrets_sa_role_arn = build_role_arn(
            props.target_account.account, "secrets-manager-sa-role")


        # Add wave for configuring EKS cluster
        config_eks_cluster = pipeline.add_wave("config-eks-cluster-wave")
        config_eks_cluster.add_post(
            pipelines.CodeBuildStep(
                "config-eks-cluster-step",
                project_name=f"{props.resource_prefix}-config-eks-cluster",
                build_environment=codebuild.BuildEnvironment(
                    build_image=codebuild.LinuxBuildImage.STANDARD_7_0
                ),
                commands=[
                    "cd scripts/cluster_config",
                    "chmod +x config_eks_cluster.sh",
                    "./config_eks_cluster.sh",
                ],
                env={
                    "ACM_CERTIFICATE_ARN": props.path_builder.get_ssm_path("acm", "certificate-arn"),
                    "AWS_REGION": props.target_account.region,
                    "CLUSTER_ACCESS_ROLE_ARN": cluster_access_role_arn,
                    "CLUSTER_NAME": f"{props.resource_prefix}-cluster",
                    "EFS_FILE_SYSTEM_PARAM": props.path_builder.get_ssm_path("efs", "file-system-id"),
                    "EXTERNAL_DNS_ROLE": external_dns_role_arn,
                    "EXTERNAL_DNS_SA_ROLE": external_dns_sa_role_arn,
                    "EXTERNAL_SECRETS_SA_ROLE": external_secrets_sa_role_arn,
                    "KARPENTER_VERSION": props.karpenter_version,
                    "KUBERNETES_VERSION": props.kubernetes_version,
                    "RESOURCE_PREFIX": props.resource_prefix,
                    "TARGET_ACCOUNT_ID": props.target_account.account,
                },
                role=codebuild_role,
            )
        )

        if props.enable_manual_approval:
            # Add manual approval stage
            approval_stage = pipeline.add_wave("eks-cluster-approval-wave")
            approval_stage.add_pre(
                pipelines.ManualApprovalStep(
                    "eks-cluster-approval-step",
                    comment="Approve?",
                )
            )

        # Add wave for deploying apps into EKS cluster
        deploy_apps_into_cluster = pipeline.add_wave("deploy-apps-into-cluster-wave")
        deploy_apps_into_cluster.add_post(
            pipelines.CodeBuildStep(
                "deploy-apps-into-cluster-step",
                project_name=f"{props.resource_prefix}-deploy-apps-into-cluster",
                build_environment=codebuild.BuildEnvironment(
                    build_image=codebuild.LinuxBuildImage.STANDARD_7_0
                ),
                commands=[
                    "cd scripts/cluster_config",
                    "chmod +x deploy_apps_into_cluster.sh",
                    "./deploy_apps_into_cluster.sh",
                ],
                env={
                    "ACM_CERTIFICATE_ARN": props.path_builder.get_ssm_path("acm", "certificate-arn"),
                    "AWS_REGION": props.target_account.region,
                    "CLUSTER_ACCESS_ROLE_ARN": cluster_access_role_arn,
                    "CLUSTER_NAME": f"{props.resource_prefix}-cluster",
                    "RESOURCE_PREFIX": props.resource_prefix,
                    "TARGET_ACCOUNT_ID": props.target_account.account,
                },
                role=codebuild_role,
            )
        )

        if props.enable_manual_approval:
            pipeline.build_pipeline()

            # Create SNS topic for approval notifications
            approval_topic = sns.Topic(
                self,
                "EKSClusterApprovalTopic",
                display_name=f"{props.resource_prefix}-eks-cluster-approval",
                topic_name=f"{props.resource_prefix}-eks-cluster-approval"
            )
            for subscription in props.email_subscriptions:
                approval_topic.add_subscription(
                    subscriptions.EmailSubscription(subscription))

            # Create notification rule
            notifications.NotificationRule(
                self,
                "notification-rule",
                events=["codepipeline-pipeline-pipeline-execution-started",
                        "codepipeline-pipeline-pipeline-execution-failed",
                        "codepipeline-pipeline-pipeline-execution-succeeded",
                        "codepipeline-pipeline-manual-approval-needed"],
                targets=[approval_topic],
                source=pipeline.pipeline,
                notification_rule_name=f"{props.resource_prefix}-notification-rule",
            )
