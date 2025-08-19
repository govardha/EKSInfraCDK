from aws_cdk import (
    aws_ec2 as ec2,
    Fn,
    Stack,
)
from constructs import Construct
from dataclasses import dataclass


@dataclass
class VpcImportStackProps:
    resource_prefix: str
    project_tags: dict
    number_of_azs: int = 2


class VpcImportStack(Stack):
    @property
    def vpc(self):
        """Store the VPC object as a property of the stack"""
        return self._vpc

    def __init__(self, scope: Construct, construct_id: str,
                 props: VpcImportStackProps, **kwargs) -> None:
        super().__init__(scope, construct_id,
                         tags=props.project_tags, **kwargs)

        # Import the VPC ID from CloudFormation exports
        vpc_id = Fn.import_value(f"{props.resource_prefix}-vpc-id")

        # Import VPC CIDR block
        vpc_cidr = Fn.import_value(f"{props.resource_prefix}-vpc-cidr-range")

        # Import availability zones
        availability_zones = Fn.import_list_value(
            f"{props.resource_prefix}-availability-zones",
            assumed_length=props.number_of_azs,
        )

        # Import Public Subnet IDs and their route tables
        public_subnet_ids = Fn.import_list_value(
            f"{props.resource_prefix}-public-subnet-ids",
            assumed_length=props.number_of_azs,
        )
        public_route_table_ids = Fn.import_list_value(
            f"{props.resource_prefix}-public-route-tables",
            assumed_length=props.number_of_azs,
        )

        # Import Private (with egress) Subnet IDs and their route tables
        private_subnet_ids = Fn.import_list_value(
            f"{props.resource_prefix}-private-subnet-ids",
            assumed_length=props.number_of_azs,
        )
        private_route_table_ids = Fn.import_list_value(
            f"{props.resource_prefix}-private-route-tables",
            assumed_length=props.number_of_azs,
        )

        # Import Database (isolated) Subnet IDs and their route tables
        database_subnet_ids = Fn.import_list_value(
            f"{props.resource_prefix}-database-subnet-ids",
            assumed_length=props.number_of_azs,
        )
        database_route_table_ids = Fn.import_list_value(
            f"{props.resource_prefix}-database-route-tables",
            assumed_length=props.number_of_azs,
        )

        # Import the VPC with all subnet groups provided
        self._vpc = ec2.Vpc.from_vpc_attributes(
            self,
            f"{props.resource_prefix}-vpc",
            vpc_id=vpc_id,
            vpc_cidr_block=vpc_cidr,
            availability_zones=availability_zones,
            public_subnet_ids=public_subnet_ids,
            public_subnet_route_table_ids=public_route_table_ids,
            private_subnet_ids=private_subnet_ids,
            private_subnet_route_table_ids=private_route_table_ids,
            isolated_subnet_ids=database_subnet_ids,
            isolated_subnet_route_table_ids=database_route_table_ids,
        )
