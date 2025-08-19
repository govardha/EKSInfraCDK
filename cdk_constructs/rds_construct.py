import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_rds as rds,
    custom_resources as cr,
    CustomResource,
    Duration,
    Stack,
)
from constructs import Construct

from config.path_builder import PathBuilder


@dataclass
class RdsConstructProps:
    application_name: str
    database_name: str
    path_builder: PathBuilder
    project_tags: Dict[str, str]
    rds_allocated_storage: int
    rds_backup_retention_days: int
    rds_postgres_instance_type: str
    resource_prefix: str
    tenant_id: str
    vpc: ec2.Vpc
    db_script_name: Optional[str] = None
    postgres_version: str = "17_2"


class RdsConstruct(Construct):
    def __init__(self, scope: Construct, id_: str, *,
                 props: RdsConstructProps) -> None:
        super().__init__(scope, id_)

        # Create a security group for RDS
        security_group = ec2.SecurityGroup(
            self,
            "RDSSecurityGroup",
            vpc=props.vpc,
            security_group_name=f"{props.resource_prefix}-{props.application_name}-security-group",
            description=f"Security group for {props.application_name} RDS PostgreSQL instance",
            allow_all_outbound=True,
        )

        # Allow inbound PostgreSQL traffic within the VPC
        security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(props.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(5432),
            description="Allow PostgreSQL access from within VPC",
        )

        # Create credentials with a random password
        secret_name = props.path_builder.get_ssm_path(
            "rds", f"{props.application_name}-db-credentials")
        credentials = rds.Credentials.from_generated_secret(
            username="postgres",
            exclude_characters="!@#$%^&*()`~,}{[]=+'?\\/|<>:;\"",
            secret_name=secret_name,
        )

        # Convert postgres_version to a valid rds.PostgresEngineVersion
        pg_version = eval(
            f"rds.PostgresEngineVersion.VER_{props.postgres_version}")

        # Create the RDS instance
        db_instance = rds.DatabaseInstance(
            self,
            "rds-instance",
            engine=rds.DatabaseInstanceEngine.postgres(version=pg_version),
            instance_type=ec2.InstanceType(props.rds_postgres_instance_type),
            vpc=props.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            max_allocated_storage=props.rds_allocated_storage,
            database_name=props.database_name,
            instance_identifier=(
                f"{props.resource_prefix}-{props.application_name}-postgres-db"
            ),
            backup_retention=Duration.days(props.rds_backup_retention_days),
            security_groups=[security_group],
            publicly_accessible=False,
            credentials=credentials,
        )

        # If no db_script_name is provided, skip the Lambda + CustomResource
        if props.db_script_name:
            # Role for the Lambda + custom resource
            role = iam.Role(
                self,
                "LambdaExecutionRole",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            )
            role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            )

            # Create a psycopg2 layer
            dirname = os.path.dirname(__file__)
            postgres_layer = _lambda.LayerVersion(
                self,
                "psycopg2-layer",
                layer_version_name=f"{props.resource_prefix}-{props.application_name}-psycopg2-layer",
                code=_lambda.Code.from_asset(
                    f"{dirname}/../lambdas/lambda-layer/psycopg2/python.zip"
                ),
                compatible_runtimes=[_lambda.Runtime.PYTHON_3_11],
            )

            # Lambda function to execute SQL script
            on_event = _lambda.Function(
                self,
                "DbInitializerFunction",
                runtime=_lambda.Runtime.PYTHON_3_11,
                handler="lambda_function.handler",
                code=_lambda.Code.from_asset(
                    os.path.join(dirname, "..", "lambdas", "db_initializer")
                ),
                vpc=props.vpc,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                security_groups=[security_group],
                environment={
                    "SECRETS_NAME": secret_name,
                    "REGION": Stack.of(self).region,
                    "DB_SCRIPT_NAME": props.db_script_name,
                },
                timeout=Duration.minutes(5),
                log_retention=logs.RetentionDays.TWO_WEEKS,
                function_name=f"{props.resource_prefix}-{props.application_name}-db-initializer",
                layers=[postgres_layer],
            )

            # Grant the Lambda function access to the RDS secret
            db_instance.secret.grant_read(on_event)

            # Additional policy for RDS data calls, etc.
            on_event.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "rds-data:ExecuteStatement",
                        "cloudwatch:PutMetricData",
                        "ds:CreateComputer",
                        "ds:DescribeDirectories",
                        "ec2:DescribeInstanceStatus",
                        "logs:*",
                        "ssm:*",
                        "ec2messages:*",
                        "secretsmanager:*",
                        "kms:Decrypt",
                    ],
                    resources=[db_instance.instance_arn],
                )
            )

            # Custom resource provider
            provider = cr.Provider(
                self,
                "DbCustomResourceProvider",
                on_event_handler=on_event,
                log_retention=logs.RetentionDays.TWO_WEEKS,
                role=role,
            )

            # Custom resource to trigger the Lambda
            #   (on every deploy if timestamp changes)
            CustomResource(
                self,
                "DbCustomResource",
                service_token=provider.service_token,
                properties={
                    "timestamp": str(time.time()).replace(".", ""),
                },
            )
