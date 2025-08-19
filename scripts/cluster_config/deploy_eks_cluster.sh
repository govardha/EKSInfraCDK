#!/bin/bash
set -e

# -----------------------------------------------------------------------------
# Deploy Karpenter CloudFormation template
# -----------------------------------------------------------------------------
echo "Deploying Karpenter CloudFormation stack..."
TEMPOUT="$(mktemp)"
curl -fsSL \
  "https://raw.githubusercontent.com/aws/karpenter-provider-aws/v${KARPENTER_VERSION}/website/content/en/preview/getting-started/getting-started-with-karpenter/cloudformation.yaml" \
  > "${TEMPOUT}"

# -----------------------------------------------------------------------------
# Assume cross-account role in target account
# -----------------------------------------------------------------------------
echo "Assuming role ${CLUSTER_ACCESS_ROLE_ARN} in target account ${TARGET_ACCOUNT_ID}..."
CREDS_JSON="$(aws sts assume-role \
  --role-arn "${CLUSTER_ACCESS_ROLE_ARN}" \
  --role-session-name "AddonsCrossAcctSession" \
  --query "Credentials" \
  --output json)"

export AWS_ACCESS_KEY_ID="$(echo "${CREDS_JSON}" | jq -r '.AccessKeyId')"
export AWS_SECRET_ACCESS_KEY="$(echo "${CREDS_JSON}" | jq -r '.SecretAccessKey')"
export AWS_SESSION_TOKEN="$(echo "${CREDS_JSON}" | jq -r '.SessionToken')"

# -----------------------------------------------------------------------------
# Retrieve VPC, subnets, and security group
# -----------------------------------------------------------------------------
echo "Retrieving VPC and subnet IDs…"

export VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=tag:Name,Values=${RESOURCE_PREFIX}-vpc" \
  --query 'Vpcs[0].VpcId' --output text)

# Get the two private subnets (sorted by AZ) without losing variables to a subshell
while read -r az subnet; do
  if [[ -z ${VPC_AZ_1:-} ]]; then
    VPC_AZ_1="$az"
    SUBNET_ID_1="$subnet"
  else
    VPC_AZ_2="$az"
    SUBNET_ID_2="$subnet"
  fi
done < <(
  aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" \
             "Name=tag:Name,Values=*PrivateSubnet*" \
    --query 'sort_by(Subnets,&AvailabilityZone)[].[AvailabilityZone,SubnetId]' \
    --output text
)

export SG_ID=$(aws ec2 describe-security-groups \
  --filters Name=group-name,Values="${RESOURCE_PREFIX}-eks-sg" \
  --query 'SecurityGroups[0].GroupId' \
  --output text)

# Export for envsubst
export VPC_AZ_1 VPC_AZ_2 SUBNET_ID_1 SUBNET_ID_2 SG_ID

# -----------------------------------------------------------------------------
# Process cluster config template
# -----------------------------------------------------------------------------
echo "Processing the cluster config template..."
envsubst < cluster_config.yaml > cluster_config_processed.yaml
cat cluster_config_processed.yaml

# -----------------------------------------------------------------------------
# Create or Update EKS Cluster via eksctl
# -----------------------------------------------------------------------------
echo "Creating or updating EKS cluster..."
if aws cloudformation describe-stacks \
  --stack-name "eksctl-${CLUSTER_NAME}-cluster" \
  --region "${AWS_REGION}" &>/dev/null; then
  echo "Stack eksctl-${CLUSTER_NAME}-cluster exists. Checking if EKS cluster exists..."
  if aws eks describe-cluster \
    --name "${CLUSTER_NAME}" \
    --region "${AWS_REGION}" &>/dev/null; then
    echo "EKS cluster ${CLUSTER_NAME} exists. Upgrading cluster..."
    eksctl upgrade cluster -f cluster_config_processed.yaml
  else
    echo "EKS cluster ${CLUSTER_NAME} not found, but CloudFormation stack exists."
    echo "Deploying Karpenter CloudFormation stack and creating new cluster..."
    aws cloudformation deploy \
      --stack-name "Karpenter-${CLUSTER_NAME}" \
      --template-file "${TEMPOUT}" \
      --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides "ClusterName=${CLUSTER_NAME}"
    eksctl create cluster -f cluster_config_processed.yaml
  fi
else
  echo "Stack eksctl-${CLUSTER_NAME}-cluster does not exist."
  echo "Deploying Karpenter CloudFormation stack and creating new cluster..."
  aws cloudformation deploy \
    --stack-name "Karpenter-${CLUSTER_NAME}" \
    --template-file "${TEMPOUT}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "ClusterName=${CLUSTER_NAME}"
  eksctl create cluster -f cluster_config_processed.yaml
fi

OIDC_ID="$(aws eks describe-cluster \
  --name "${CLUSTER_NAME}" \
  --region "${AWS_REGION}" \
  --query "cluster.identity.oidc.issuer" \
  --output text | cut -d '/' -f 5)"

echo "Storing OIDC ID in SSM..."
aws ssm put-parameter \
  --name "${OIDC_ID_PARAM}" \
  --type "String" \
  --value "${OIDC_ID}" \
  --region "${AWS_REGION}" \
  --overwrite

echo "Deployment script completed."
