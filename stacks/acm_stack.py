from dataclasses import dataclass
import os

from aws_cdk import (
    Duration,
    Stack,
    aws_lambda_python_alpha as lambda_python,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_events as events,
    aws_events_targets as targets,
    aws_certificatemanager as acm,
    aws_iam as iam,
    aws_ssm as ssm,
)
from constructs import Construct

from config.path_builder import PathBuilder


@dataclass
class AcmStackProps:
    deployment_account_id: str
    hosted_zone_name: str
    path_builder: PathBuilder
    resource_prefix: str
    project_tags: dict


class AcmStack(Stack):
    @property
    def acm_certificate(self):
        """Return the ACM certificate"""
        return self._acm_certificate

    def __init__(self, scope: Construct, construct_id: str,
                 props: AcmStackProps, **kwargs) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Construct ARN for lambda DNS validation role in zone account
        role_arn = (
            f"arn:aws:iam::{props.deployment_account_id}:role/"
            f"{props.resource_prefix}-dns-validation-role"
        )

        # Create lambda execution role in environment account
        dns_lambda_role = iam.Role(
            self,
            "dns-validation-lambda-role",
            description="Role to allow the lambda to validate acm cert",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                # Grant basic Lambda execution permissions
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                # Grant read-only access to ACM
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSCertificateManagerReadOnly"
                ),
            ],
            inline_policies={
                # Allow assuming the DNS validation role in the zone account
                "AssumeRolePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["sts:AssumeRole"],
                            resources=[role_arn],
                        )
                    ]
                ),
            },
        )

        # Create lambda to validate ACM cert
        dirname = os.path.dirname(__file__) or "."
        validate_cert_lambda = lambda_python.PythonFunction(
            self,
            "validate-cert-lambda",
            entry=f"{dirname}/../lambdas/validate_cert",
            runtime=lambda_.Runtime.PYTHON_3_12,
            log_retention=logs.RetentionDays.ONE_MONTH,
            role=dns_lambda_role,
            description="Lambda to validate DNS",
            timeout=Duration.seconds(900),
            environment={
                "HOSTED_ZONE_NAME": props.hosted_zone_name,
                "TARGET_ROLE_ARN": role_arn,
            },
        )

        # Create EventBridge rule that triggers on cert request or delete
        acm_rule = events.Rule(
            self,
            f"{props.resource_prefix}-acm-update-dns-event-rule",
            rule_name=f"{props.resource_prefix}-acm-update-dns-event-rule",
            event_pattern=events.EventPattern(
                source=["aws.acm"],
                detail_type=["AWS API Call via CloudTrail"],
                detail={
                    "eventSource": ["acm.amazonaws.com"],
                    "eventName": ["RequestCertificate", "DeleteCertificate"],
                },
            ),
        )

        # Create ACM Certificate for wildcard subdomain
        self._acm_certificate = acm.Certificate(
            self,
            f"{props.resource_prefix}-certificate",
            domain_name=f"*.{props.resource_prefix}.{props.hosted_zone_name}",
            validation=acm.CertificateValidation.from_dns(),
        )
        # Ensure the EventBridge rule is created before the certificate
        self._acm_certificate.node.add_dependency(acm_rule)
        # Add the Lambda function as a target for the EventBridge rule
        acm_rule.add_target(targets.LambdaFunction(validate_cert_lambda))

        # Create SSM parameter for ACM certificate ARN
        self.ssm_parameter = ssm.StringParameter(
            self, f"{props.resource_prefix}-acm-certificate-arn",
            parameter_name=props.path_builder.get_ssm_path(
                "acm", "certificate-arn"),
            string_value=self._acm_certificate.certificate_arn,
        )
        self.ssm_parameter.node.add_dependency(self._acm_certificate)
