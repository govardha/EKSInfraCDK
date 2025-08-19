from dataclasses import dataclass
from typing import Dict

from aws_cdk import (
    RemovalPolicy,
    aws_docdb as docdb,
    aws_ec2 as ec2,
)
from constructs import Construct
from config.path_builder import PathBuilder


@dataclass
class DocumentDbConstructProps:
    application_name: str
    path_builder: PathBuilder
    project_tags: Dict[str, str]
    resource_prefix: str
    target_env: str
    tenant_id: str
    vpc: ec2.Vpc
    allow_inbound_from_vpc: bool = True
    documentdb_instance_class: str = "T3"
    documentdb_instance_size: str = "MICRO"
    removal_policy_destroy: bool = True


class DocumentDbConstruct(Construct):
    def __init__(self, scope: Construct, id_: str, *,
                 props: DocumentDbConstructProps) -> None:
        super().__init__(scope, id_)

        # Create a parameter group with TLS disabled
        parameter_group = docdb.ClusterParameterGroup(
            self,
            "documentdb-parameter-group",
            family="docdb5.0",
            description="DocumentDB parameter group with TLS disabled",
            parameters={
                "tls": "disabled"
            },
            db_cluster_parameter_group_name=(
                f"{props.resource_prefix}-documentdb-{props.application_name}-parameter-group"
            ),
        )

        # Security group
        security_group = ec2.SecurityGroup(
            self,
            "documentdb-security-group",
            vpc=props.vpc,
            security_group_name=(
                f"{props.resource_prefix}-documentdb-{props.application_name}-security-group"
            ),
            allow_all_outbound=True,
        )

        # Optionally allow inbound from the entire VPC on port 27017
        if props.allow_inbound_from_vpc:
            security_group.add_ingress_rule(
                peer=ec2.Peer.ipv4(props.vpc.vpc_cidr_block),
                connection=ec2.Port.tcp(27017),
                description="Allow documentdb access from within VPC",
            )

        # Determine removal policy
        removal_policy = (
            RemovalPolicy.DESTROY
            if props.removal_policy_destroy
            else RemovalPolicy.SNAPSHOT
        )

        # Create the DocumentDB cluster
        self.docdb_cluster = docdb.DatabaseCluster(
            self,
            "documentdb-cluster",
            master_user=docdb.Login(
                username="backend",
                exclude_characters="!@#$%^&*()`~,}{[]=+'?\\/|<>:;\"",
                secret_name=props.path_builder.get_ssm_path(
                    "documentdb", f"{props.application_name}-db-credentials"
                ),
            ),
            instance_type=ec2.InstanceType.of(
                getattr(ec2.InstanceClass, props.documentdb_instance_class),
                getattr(ec2.InstanceSize, props.documentdb_instance_size),
            ),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            vpc=props.vpc,
            removal_policy=removal_policy,
            security_group=security_group,
            db_cluster_name=f"{props.resource_prefix}-documentdb-{props.application_name}-cluster",
            instance_identifier_base=f"{props.resource_prefix}-{props.application_name}-storage",
            instances=1,
            port=27017,
            parameter_group=parameter_group,
        )
