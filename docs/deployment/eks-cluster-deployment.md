# EKS Cluster Deployment with CDK Pipelines and eksctl

This document explains how Amazon EKS clusters are built and configured using AWS CDK Pipelines with eksctl in a multi-stage deployment process.

## Overview

The EKS cluster deployment follows a wave-based approach using AWS CDK Pipelines, where each wave represents a logical grouping of deployment steps that can run in parallel or sequence. The deployment process is broken down into three main waves:

1. **Infrastructure Wave** - Deploys base infrastructure (VPC, IAM roles, etc.)
2. **EKS Cluster Deployment Wave** - Creates the EKS cluster using eksctl
3. **Cluster Configuration Wave** - Installs and configures cluster add-ons
4. **Application Deployment Wave** - Deploys applications into the cluster

## Architecture Components

### Pipeline Structure

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   Network Stage     │ -> │   Infrastructure    │ -> │   Post-Deploy       │
│   - VPC             │    │   Stage             │    │   Stage             │
│   - DNS Roles       │    │   - EFS             │    │   - External DNS    │
│                     │    │   - Secrets         │    │   - Service Accts   │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EKS Deployment Waves                               │
├─────────────────────┬─────────────────────┬─────────────────────────────────┤
│  deploy-eks-        │  config-eks-        │  deploy-apps-into-              │
│  cluster-wave       │  cluster-wave       │  cluster-wave                   │
│                     │                     │                                 │
│  - Create EKS       │  - Install Addons   │  - Deploy Applications          │
│  - Deploy Karpenter │  - Configure RBAC   │  - Setup GitOps                 │
│  - Setup OIDC       │  - Setup Storage    │  - Install Observability       │
└─────────────────────┴─────────────────────┴─────────────────────────────────┘
```

### Key Files and Their Roles

| File | Purpose | Wave |
|------|---------|------|
| `cluster_config.yaml` | eksctl cluster configuration template | Deploy |
| `deploy_eks_cluster.sh` | Creates EKS cluster with eksctl | Deploy |
| `config_eks_cluster.sh` | Installs cluster add-ons and configurations | Config |
| `deploy_apps_into_cluster.sh` | Deploys applications and GitOps setup | Apps |

## Wave 1: EKS Cluster Deployment

### Purpose
Creates the core EKS cluster infrastructure using eksctl with a declarative configuration.

### Process Flow

1. **Role Assumption**: Assumes cross-account role for target environment
2. **Infrastructure Discovery**: Retrieves VPC, subnet, and security group information
3. **Template Processing**: Processes `cluster_config.yaml` with environment variables
4. **Cluster Creation**: Uses eksctl to create or update the cluster
5. **OIDC Configuration**: Stores OIDC provider ID in SSM Parameter Store

### Key Components

#### Cluster Configuration (`cluster_config.yaml`)
```yaml
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: ${CLUSTER_NAME}
  region: ${AWS_REGION}
  version: "${KUBERNETES_VERSION}"
vpc:
  id: ${VPC_ID}
  subnets:
    private:
      ${VPC_AZ_1}: { id: ${SUBNET_ID_1} }
      ${VPC_AZ_2}: { id: ${SUBNET_ID_2} }
```

#### Managed Node Groups
- **System Node Group**: Hosts core Kubernetes services
- **Instance Type**: m5.xlarge
- **Scaling**: 1-4 nodes with 2 desired capacity
- **Networking**: Private subnets only

#### Service Accounts with IRSA
- AWS Load Balancer Controller
- External Secrets Operator
- Cert Manager
- Cluster Autoscaler

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `CLUSTER_NAME` | Name of the EKS cluster | `tenant-1-dev-cluster` |
| `CLUSTER_ACCESS_ROLE_ARN` | Cross-account role for cluster access | `arn:aws:iam::123456789012:role/...` |
| `KUBERNETES_VERSION` | Kubernetes version | `1.32` |
| `KARPENTER_VERSION` | Karpenter version for autoscaling | `1.2.1` |

## Wave 2: Cluster Configuration

### Purpose
Installs and configures essential cluster add-ons and prepares the cluster for workloads.

### Process Flow

1. **Kubectl Configuration**: Updates kubeconfig for cluster access
2. **Storage Classes**: Creates EFS, GP2, and GP3 storage classes
3. **Namespace Creation**: Sets up namespaces with proper security policies
4. **Add-on Installation**: Installs Karpenter, AWS Load Balancer Controller, ExternalDNS
5. **Security Configuration**: Applies Pod Security Standards
6. **GitOps Setup**: Deploys ArgoCD for application management

### Key Add-ons Installed

#### Karpenter (Node Autoscaling)
- **Purpose**: Automatically provisions EC2 instances for pods
- **Configuration**: Supports both general-purpose and GPU workloads
- **Node Classes**: Default (CPU) and GPU-optimized instances

#### AWS Load Balancer Controller
- **Purpose**: Manages Application Load Balancers for Ingress
- **Integration**: Works with Istio Gateway for traffic management

#### ExternalDNS
- **Purpose**: Automatically manages Route53 DNS records
- **Cross-Account**: Assumes role in DNS management account

#### External Secrets Operator
- **Purpose**: Syncs secrets from AWS Secrets Manager to Kubernetes
- **Security**: Uses IRSA for secure access to AWS services

### Security Configuration

#### Pod Security Standards
- **Restricted**: Applied to most workload namespaces
- **Baseline**: Applied to system namespaces
- **Privileged**: Applied to infrastructure namespaces (kube-system)

#### Network Policies
- **Istio Injection**: Enabled for all workload namespaces
- **Service Mesh**: Provides secure service-to-service communication

## Wave 3: Application Deployment

### Purpose
Deploys applications and sets up GitOps workflows for ongoing management.

### Process Flow

1. **GitOps Repository Setup**: Clones and configures application manifests
2. **Certificate Management**: Retrieves SSL certificates from ACM
3. **Application Deployment**: Uses ArgoCD ApplicationSets for deployment
4. **Observability Stack**: Installs Prometheus, Grafana, and Loki
5. **Service Configuration**: Configures Keycloak, databases, and other services

### GitOps Workflow

#### ArgoCD Configuration
- **Repository**: Private Git repository with application manifests
- **Branch Strategy**: Creates environment-specific branches
- **Application Sets**: Manages multiple applications with templating

#### Application Structure
```
applications/
├── istio/
│   ├── istio-app/
│   └── istio-gateway/
├── observability/
│   ├── prometheus-values.yaml
│   └── loki-values.yaml
└── products/
    ├── product-a/
    └── product-b/
```

## Cross-Account Security

### IAM Role Structure

#### Deployment Account Roles
- **Pipeline Role**: Executes CDK deployments
- **CodeBuild Role**: Runs cluster configuration scripts
- **DNS Validation Role**: Manages Route53 records for certificates

#### Target Account Roles
- **Cluster Access Role**: Allows CodeBuild to manage EKS cluster
- **Service Account Roles**: IRSA roles for Kubernetes services
- **Node Instance Role**: EC2 instance role for worker nodes

### Security Best Practices

1. **Least Privilege**: Each role has minimal required permissions
2. **Cross-Account Access**: Secure assume-role patterns
3. **Resource Isolation**: Tenant-specific resource prefixes
4. **Network Security**: Private subnets and security groups
5. **Secret Management**: AWS Secrets Manager integration

## Monitoring and Observability

### Cluster Logging
- **Control Plane Logs**: Audit, authenticator, scheduler, API server
- **Retention**: 14 days in CloudWatch Logs
- **VPC Flow Logs**: Network traffic monitoring

### Metrics and Monitoring
- **Prometheus**: Metrics collection and storage
- **Grafana**: Visualization and dashboards
- **CloudWatch**: AWS service metrics and alarms

### Distributed Tracing
- **Istio**: Service mesh provides distributed tracing
- **Jaeger**: Tracing visualization (optional)

## Troubleshooting

### Common Issues

#### Cluster Creation Failures
1. **VPC Configuration**: Verify subnet and security group existence
2. **IAM Permissions**: Check CodeBuild role permissions
3. **Resource Limits**: Ensure account limits for EKS resources

#### Add-on Installation Issues
1. **IRSA Configuration**: Verify OIDC provider setup
2. **Network Connectivity**: Check VPC endpoints and NAT gateways
3. **Resource Conflicts**: Ensure unique resource names

### Debugging Commands

```bash
# Check cluster status
aws eks describe-cluster --name ${CLUSTER_NAME}

# Verify node groups
kubectl get nodes

# Check add-on status
kubectl get pods -A

# Review CodeBuild logs
aws logs describe-log-groups --log-group-name-prefix /aws/codebuild/
```

## Maintenance and Updates

### Cluster Updates
- **Kubernetes Version**: Update through eksctl configuration
- **Node Group Updates**: Managed through eksctl or Karpenter
- **Add-on Updates**: Helm chart version management

### Security Patching
- **Node Images**: Automatic updates through managed node groups
- **Container Images**: Regular security scanning and updates
- **Add-on Updates**: Scheduled maintenance windows

This wave-based approach ensures reliable, repeatable, and secure EKS cluster deployments while maintaining separation of concerns between infrastructure, cluster configuration, and application deployment phases.
