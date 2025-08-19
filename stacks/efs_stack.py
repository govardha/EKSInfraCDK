from dataclasses import dataclass

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_efs as efs,
    aws_ssm as ssm,
    RemovalPolicy
)
from constructs import Construct

from config.path_builder import PathBuilder


@dataclass
class EfsStackProps:
    resource_prefix: str
    path_builder: PathBuilder
    project_tags: dict
    vpc: ec2.Vpc


class EfsStack(Stack):
    @property
    def efs_file_system(self) -> efs.FileSystem:
        """Expose the EFS FileSystem object."""
        return self._efs_file_system

    def __init__(self, scope: Construct, construct_id: str,
                 props: EfsStackProps, **kwargs) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Create a Security Group for EFS
        efs_sg = ec2.SecurityGroup(
            self,
            "EfsSecurityGroup",
            vpc=props.vpc,
            description="Security group for EFS",
            allow_all_outbound=True,
            security_group_name=f"{props.resource_prefix}-efs-sg",
        )
        efs_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(props.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2049),
            description="Allow NFS access from within the VPC",
        )

        # Create the EFS FileSystem
        self._efs_file_system = efs.FileSystem(
            self,
            "EfsFileSystem",
            vpc=props.vpc,
            security_group=efs_sg,
            file_system_name=f"{props.resource_prefix}-efs-fs",
            removal_policy=RemovalPolicy.DESTROY,
            # performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            # throughput_mode=efs.ThroughputMode.BURSTING,
            # lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,
            # encrypted=True
        )

        # Create an SSM Parameter for the EFS file system ID
        ssm.StringParameter(
            self,
            "EfsFileSystemIdParameter",
            parameter_name=props.path_builder.get_ssm_path(
                "efs", "file-system-id"),
            string_value=self._efs_file_system.file_system_id,
        )
