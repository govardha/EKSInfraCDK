import json
from dataclasses import dataclass

from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_ssm as ssm,
    CfnOutput,
    RemovalPolicy,
    Stack,
    Tags,
)
from constructs import Construct

from config.path_builder import PathBuilder


@dataclass
class VpcStackProps:
    path_builder: PathBuilder
    project_tags: dict
    resource_prefix: str
    target_env: str
    tenant_id: str
    cidr_mask: int = 20
    max_azs: int = 2
    nat_gateways: int = 1


class VpcStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 props: VpcStackProps, **kwargs) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Setup IAM role for logs
        vpc_flow_role = iam.Role(
            self,
            "FlowLog",
            assumed_by=iam.ServicePrincipal("vpc-flow-logs.amazonaws.com"),
        )

        # Create Cloudwatch log group
        log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"{props.resource_prefix}-vpc-flow-log-group",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Create the VPC
        vpc = ec2.Vpc(
            self,
            f"{props.resource_prefix}-vpc",
            vpc_name=f"{props.resource_prefix}-vpc",
            max_azs=props.max_azs,
            flow_logs={
                "VpcFlowLogs": ec2.FlowLogOptions(
                    traffic_type=ec2.FlowLogTrafficType.ALL,
                    destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                        log_group=log_group, iam_role=vpc_flow_role
                    ),
                )
            },
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="PublicSubnet",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=props.cidr_mask,
                ),
                ec2.SubnetConfiguration(
                    name="PrivateSubnet",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=props.cidr_mask,
                ),
                ec2.SubnetConfiguration(
                    name="DatabaseSubnet",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=props.cidr_mask,
                ),
            ],
            nat_gateways=props.nat_gateways,
            enable_dns_support=True,
            enable_dns_hostnames=True,
        )

        # ------------------------------------------------------------------
        # Add tags required by EKS for subnets.
        # Tag public subnets with "kubernetes.io/role/elb=1"
        for public_subnet in vpc.public_subnets:
            Tags.of(public_subnet).add("kubernetes.io/role/elb", "1")

        # Tag private (with egress) subnets with
        # "kubernetes.io/role/internal-elb=1"
        for private_subnet in vpc.private_subnets:
            Tags.of(private_subnet).add("kubernetes.io/role/internal-elb", "1")
            # Add Karpenter discovery tag to private subnets
            Tags.of(private_subnet).add(
                "karpenter.sh/discovery", f"{props.resource_prefix}-cluster"
            )
        # ------------------------------------------------------------------

        # Add VPC endpoint for S3
        s3_endpoint = vpc.add_gateway_endpoint(
            "s3-endpoint", service=ec2.GatewayVpcEndpointAwsService.S3
        )

        # Create ssm parameter for S3 Gateway Endpoint ID
        ssm.StringParameter(
            self,
            "s3-endpoint-id-ssm",
            parameter_name=props.path_builder.get_ssm_path(
                "vpc", "s3-endpoint-id"),
            string_value=s3_endpoint.vpc_endpoint_id,
        )

        # Add Interface Endpoints for Various Services
        interface_endpoints = [
            "ec2",
            "ec2messages",
            "ecr.api",
            "ecr.dkr",
            "eks",
            "lambda",
            "logs",
            "ssm",
            "ssmmessages",
            "guardduty-data"
        ]

        # Create the interface endpoints
        for service in interface_endpoints:
            # Create a unique security group for each endpoint
            sg_name = f"{props.resource_prefix}-{service}-endpoint-sg"
            security_group = ec2.SecurityGroup(
                self,
                f"{service}-endpoint-sg",
                vpc=vpc,
                description=f"Security group for {service} VPC endpoint",
                security_group_name=sg_name,
            )
            Tags.of(security_group).add("Name", sg_name)

            # Add the interface endpoint with the custom security group
            vpc.add_interface_endpoint(
                f"{service}-endpoint",
                service=ec2.InterfaceVpcEndpointService(
                    name=f"com.amazonaws.{self.region}.{service}", port=443
                ),
                security_groups=[security_group],
                private_dns_enabled=True,
            )

        # Create SSM parameter for VPC ID
        ssm.StringParameter(
            self,
            "vpc-id-parameter",
            parameter_name=props.path_builder.get_ssm_path("vpc", "id"),
            string_value=vpc.vpc_id,
        )

        # CloudFormation Export for VPC ID
        CfnOutput(
            self,
            "vpc-id-export",
            value=vpc.vpc_id,
            export_name=f"{props.resource_prefix}-vpc-id",
        )

        # Export Public Subnets
        public_subnet_ids = [subnet.subnet_id for subnet in vpc.public_subnets]
        CfnOutput(
            self,
            "public-subnet-ids-export",
            value=",".join(public_subnet_ids),
            export_name=f"{props.resource_prefix}-public-subnet-ids",
        )

        # Export Public Route Tables
        public_route_table_ids = [
            subnet.route_table.route_table_id for subnet in vpc.public_subnets
        ]
        CfnOutput(
            self,
            "public-route-table-ids-export",
            value=",".join(public_route_table_ids),
            export_name=f"{props.resource_prefix}-public-route-tables",
        )

        # Export Private (with egress) Subnets
        private_subnet_ids = [
            subnet.subnet_id for subnet in vpc.private_subnets]
        CfnOutput(
            self,
            "private-subnet-ids-export",
            value=",".join(private_subnet_ids),
            export_name=f"{props.resource_prefix}-private-subnet-ids",
        )

        # Export Private (with egress) Route Tables
        private_route_table_ids = [
            subnet.route_table.route_table_id for subnet in vpc.private_subnets
        ]
        CfnOutput(
            self,
            "private-route-table-ids-export",
            value=",".join(private_route_table_ids),
            export_name=f"{props.resource_prefix}-private-route-tables",
        )

        # Export Database (isolated) Subnets
        database_subnet_ids = [
            subnet.subnet_id for subnet in vpc.isolated_subnets]
        CfnOutput(
            self,
            "database-subnet-ids-export",
            value=",".join(database_subnet_ids),
            export_name=f"{props.resource_prefix}-database-subnet-ids",
        )

        # Export Database (isolated) Route Tables
        database_route_table_ids = [
            subnet.route_table.route_table_id
            for subnet in vpc.isolated_subnets
        ]

        CfnOutput(
            self,
            "database-route-table-ids-export",
            value=",".join(database_route_table_ids),
            export_name=f"{props.resource_prefix}-database-route-tables",
        )

        # Store the VPC CIDR block as an SSM parameter
        ssm.StringParameter(
            self,
            "vpc-cidr-block-ssm-parameter",
            parameter_name=props.path_builder.get_ssm_path(
                "vpc", "cidr-block"),
            string_value=vpc.vpc_cidr_block,
        )

        # Store the list of availability zones as an SSM parameter
        availability_zones = vpc.availability_zones
        ssm.StringParameter(
            self,
            "availability-zones-ssm-parameter",
            parameter_name=props.path_builder.get_ssm_path(
                "vpc", "availability-zones"),
            string_value=json.dumps(availability_zones),
        )

        # CloudFormation Export for Availability Zones
        CfnOutput(
            self,
            "availability-zones-export",
            value=",".join(availability_zones),
            export_name=f"{props.resource_prefix}-availability-zones",
        )

        # Export VPC CIDR range
        CfnOutput(
            self,
            "vpc-cidr-range-export",
            value=vpc.vpc_cidr_block,
            export_name=f"{props.resource_prefix}-vpc-cidr-range",
        )
