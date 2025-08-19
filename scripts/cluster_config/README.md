# `cluster_config` Folder Documentation

This folder contains configuration files and scripts for setting up and managing an Amazon EKS (Elastic Kubernetes Service) cluster and deploying applications into it. Below is an overview of the files in this folder and their purposes.

## Files and Their Purposes

### 1. `cluster_config.yaml`
- **Purpose**:
  - Defines the configuration for the EKS cluster, including metadata, VPC settings, IAM roles, managed node groups, and Kubernetes addons.
  - Specifies storage classes, logging configurations, and pod identity mappings.
- **Key Features**:
  - Supports private networking for node groups.
  - Configures IAM roles for services like Karpenter, AWS Load Balancer Controller, and External Secrets Operator.
  - Includes managed node group definitions with scaling configurations.

---

### 2. `config_eks_cluster.sh`
- **Purpose**:
  - Automates the setup and configuration of the EKS cluster.
  - Installs and configures Kubernetes addons, Helm charts, and other dependencies.
- **Key Features**:
  - Retrieves and applies AMI IDs for Karpenter.
  - Configures storage classes (EFS, GP2, GP3).
  - Installs and configures:
    - AWS Load Balancer Controller
    - ExternalDNS
    - Karpenter
    - NVIDIA Device Plugin for GPU support
    - ArgoCD for GitOps
  - Creates namespaces with Istio injection enabled.
  - Deploys the Keycloak operator and External Secrets Operator.

---

### 3. `deploy_apps_into_cluster.sh`
- **Purpose**:
  - Deploys applications and dependencies into the EKS cluster.
  - Updates application configurations and applies Kubernetes manifests.
- **Key Features**:
  - Clones the `argocd-app-config` repository and updates application configurations.

  - Sets up observability tools like Grafana and Prometheus using Helm charts.
  - Creates Kubernetes secrets and config maps for application dependencies.
  - Waits for critical pods (e.g., Keycloak, RabbitMQ) to become ready.

---

### 4. `deploy_eks_cluster.sh`
- **Purpose**:
  - Deploys the EKS cluster using `eksctl` and a CloudFormation stack for Karpenter.
- **Key Features**:
  - Processes the `cluster_config.yaml` template and creates a processed configuration file.
  - Creates or updates the EKS cluster using `eksctl`.
  - Deploys the Karpenter CloudFormation stack.
  - Stores the OIDC ID in AWS Systems Manager (SSM) Parameter Store.

---

## Usage Instructions

1. **Cluster Configuration**:
   - Modify `cluster_config.yaml` to customize the EKS cluster settings (e.g., node groups, IAM roles, addons).

2. **Cluster Setup**:
   - Run `config_eks_cluster.sh` to set up the EKS cluster and install required addons.

3. **Cluster Deployment**:
   - Use `deploy_eks_cluster.sh` to deploy the EKS cluster and Karpenter stack.

4. **Application Deployment**:
   - Execute `deploy_apps_into_cluster.sh` to deploy applications and their dependencies into the cluster.

---

## Notes
- Ensure that all required environment variables (e.g., `CLUSTER_NAME`, `AWS_REGION`, `RESOURCE_PREFIX`) are set before running the scripts.
- The scripts rely on AWS CLI, `kubectl`, and `helm`. Ensure these tools are installed and configured on your system.
- Sensitive information (e.g., credentials) is retrieved from AWS Secrets Manager.
