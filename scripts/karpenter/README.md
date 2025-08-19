# `karpenter` Folder Documentation

This folder contains configuration files for managing Karpenter, an open-source Kubernetes cluster autoscaler. These files define node classes and node pools for provisioning EC2 instances in an Amazon EKS cluster.

## Files and Their Purposes

### 1. `ec2nodeclass.yaml`
- **Purpose**:
  - Defines the default EC2 node class for Karpenter.
  - Specifies the configuration for provisioning general-purpose EC2 instances.
- **Key Features**:
  - Uses Amazon Linux 2 (AL2) as the AMI family.
  - Configures block storage with 100 GiB of `gp3` volume.
  - Selects subnets and security groups based on tags.
  - Uses the AMI ID specified by the `${AMD_AMI_ID}` environment variable.

---

### 2. `ec2nodeclass-gpu.yaml`
- **Purpose**:
  - Defines a specialized EC2 node class for GPU-enabled workloads.
  - Specifies the configuration for provisioning GPU-optimized EC2 instances.
- **Key Features**:
  - Uses Amazon Linux 2 (AL2) as the AMI family.
  - Configures block storage with 200 GiB of `gp3` volume.
  - Selects subnets and security groups based on tags.
  - Uses the GPU-specific AMI ID specified by the `${GPU_AMI_ID}` environment variable.

---

### 3. `nodepool-default.yaml`
- **Purpose**:
  - Defines the default node pool for general-purpose workloads.
  - Specifies the requirements and limits for EC2 instances provisioned by Karpenter.
- **Key Features**:
  - Supports Linux operating systems and `amd64` architecture.
  - Restricts instance categories to `c`, `m`, and `r` families with 4 or 8 vCPUs.
  - Associates with the default EC2 node class.
  - Sets a CPU limit of 1000 cores.
  - Enables consolidation of underutilized nodes after 1 minute.

---

### 4. `nodepool-gpu.yaml`
- **Purpose**:
  - Defines a node pool for GPU-enabled workloads.
  - Specifies the requirements and taints for GPU-optimized EC2 instances.
- **Key Features**:
  - Supports Linux operating systems and `amd64` architecture.
  - Restricts instance families to `g4dn` with sizes `xlarge` and `2xlarge`.
  - Associates with the GPU-specific EC2 node class.
  - Adds a taint (`nvidia.com/gpu=true:NoSchedule`) to ensure only GPU workloads are scheduled.
  - Enables consolidation of underutilized nodes after 1 minute.

---

## Usage Instructions

1. **Node Class Configuration**:
   - Modify `ec2nodeclass.yaml` and `ec2nodeclass-gpu.yaml` to customize the EC2 instance configurations for general-purpose and GPU workloads.

2. **Node Pool Configuration**:
   - Update `nodepool-default.yaml` and `nodepool-gpu.yaml` to define workload-specific requirements and limits.

3. **Deployment**:
   - Apply the configurations using `kubectl`:
     ```bash
     kubectl apply -f ec2nodeclass.yaml
     kubectl apply -f ec2nodeclass-gpu.yaml
     kubectl apply -f nodepool-default.yaml
     kubectl apply -f nodepool-gpu.yaml
     ```

4. **Environment Variables**:
   - Ensure the following environment variables are set before applying the configurations:
     - `CLUSTER_NAME`: The name of the EKS cluster.
     - `AMD_AMI_ID`: The AMI ID for general-purpose instances.
     - `GPU_AMI_ID`: The AMI ID for GPU-optimized instances.

---

## Notes
- These configurations are designed for use with Karpenter in an Amazon EKS cluster.
- Ensure that the required IAM roles and permissions are configured for Karpenter to provision EC2 instances.
- The taints and labels in the node pools should align with the workload requirements in your Kubernetes cluster.
