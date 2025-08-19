#!/bin/bash
set -e

export KEYCLOAK_VERSION="26.0.5"   # For keycloak operator manifests
export NVIDIA_DEVICE_PLUGIN_VERSION="0.17.1"




echo "$DOCKER_HUB_PASSWORD" | docker login -u "$DOCKER_HUB_USERNAME" --password-stdin

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
# Retrieve VPC, Subnets, and Security Group
# -----------------------------------------------------------------------------
echo "Retrieving VPC and Subnet IDs..."

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
# Install Helm
# -----------------------------------------------------------------------------
echo "Installing Helm..."
curl https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 > get_helm.sh
chmod 700 get_helm.sh
./get_helm.sh
helm version

# -----------------------------------------------------------------------------
# Retrieve AMI IDs for Karpenter
# -----------------------------------------------------------------------------
echo "Getting AMI IDs for Karpenter..."
export ARM_AMI_ID="$(aws ssm get-parameter \
  --name "/aws/service/eks/optimized-ami/${KUBERNETES_VERSION}/amazon-linux-2-arm64/recommended/image_id" \
  --query Parameter.Value \
  --output text)"

export AMD_AMI_ID="$(aws ssm get-parameter \
  --name "/aws/service/eks/optimized-ami/${KUBERNETES_VERSION}/amazon-linux-2/recommended/image_id" \
  --query Parameter.Value \
  --output text)"

export GPU_AMI_ID="$(aws ssm get-parameter \
  --name "/aws/service/eks/optimized-ami/${KUBERNETES_VERSION}/amazon-linux-2-gpu/recommended/image_id" \
  --query Parameter.Value \
  --output text)"

# -----------------------------------------------------------------------------
# Configure kubectl
# -----------------------------------------------------------------------------
echo "Configuring kubectl..."
aws eks update-kubeconfig \
  --name "${CLUSTER_NAME}" \
  --region "${AWS_REGION}"

# -----------------------------------------------------------------------------
# Create EFS, GP2, and GP3 Storage Classes
# -----------------------------------------------------------------------------
echo "Creating EFS Storage Class..."

export FILE_SYSTEM_ID="$(aws ssm get-parameter \
  --name "${EFS_FILE_SYSTEM_PARAM}" \
  --query Parameter.Value \
  --output text)"

cat <<EOF | envsubst | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-sc
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap
  fileSystemId: ${FILE_SYSTEM_ID}
  directoryPerms: "700"
mountOptions:
  - iam
reclaimPolicy: Retain
volumeBindingMode: WaitForFirstConsumer
EOF

echo "Creating GP3 Storage Class..."
cat <<EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3-sc
provisioner: kubernetes.io/aws-ebs
parameters:
  type: gp3
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
EOF

echo "Creating gp2 StorageClass..."
cat <<EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp2
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: kubernetes.io/aws-ebs
parameters:
  type: gp2
  fsType: ext4
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Delete
allowVolumeExpansion: true
EOF

# -----------------------------------------------------------------------------
# Create namespaces with istio injection enabled
# -----------------------------------------------------------------------------
create_namespace_with_istio() {
  local namespace=$1
  kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
  labels:
    istio-injection: enabled
    pod-security.kubernetes.io/audit: baseline
    pod-security.kubernetes.io/audit-version: latest
    pod-security.kubernetes.io/warn: baseline
    pod-security.kubernetes.io/warn-version: latest
EOF
}


for namespace in "${namespaces[@]}"; do
  create_namespace_with_istio "$namespace"
done


# -----------------------------------------------------------------------------
# Create Docker Hub registry secret in all namespaces
# -----------------------------------------------------------------------------
echo "Creating Docker Hub registry secrets..."

# Create external-dns namespace
kubectl create namespace external-dns || true

# Add kube-system and external-dns to the list of namespaces
all_namespaces=("${namespaces[@]}" "kube-system" "external-dns")

for namespace in "${all_namespaces[@]}"; do
  # Create or update the Docker registry secret in each namespace
  kubectl create secret docker-registry dockerhub-regcred \
    --docker-server=index.docker.io \
    --docker-username="$DOCKER_HUB_USERNAME" \
    --docker-password="$DOCKER_HUB_PASSWORD" \

    -n "$namespace" \
    --dry-run=client -o yaml | kubectl apply -f -
done

# -----------------------------------------------------------------------------
# Install Karpenter
# -----------------------------------------------------------------------------
echo "Installing Karpenter..."
helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter \
  --version "${KARPENTER_VERSION}" \
  --namespace kube-system \
  --set settings.clusterName="${CLUSTER_NAME}" \
  --set settings.interruptionQueue="${CLUSTER_NAME}" \
  --set controller.resources.requests.cpu=1 \
  --set controller.resources.requests.memory=1Gi \
  --set controller.resources.limits.cpu=1 \
  --set controller.resources.limits.memory=1Gi \
  --wait

# -----------------------------------------------------------------------------
# Process and apply Karpenter configurations
# -----------------------------------------------------------------------------
echo "Processing and applying Karpenter configurations..."
cd ../karpenter
envsubst < ec2nodeclass.yaml > ec2nodeclass_processed.yaml
envsubst < ec2nodeclass-gpu.yaml > ec2nodeclass-gpu_processed.yaml
kubectl apply -f nodepool-default.yaml
kubectl apply -f nodepool-gpu.yaml
kubectl apply -f ec2nodeclass_processed.yaml
kubectl apply -f ec2nodeclass-gpu_processed.yaml

# -----------------------------------------------------------------------------
# Install NVIDIA Device Plugin for GPU support with time slicing enabled
# -----------------------------------------------------------------------------
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: nvidia-device-plugin
  namespace: kube-system
data:
  any: |-
    version: v1
    flags:
      migStrategy: none
    sharing:
      timeSlicing:
        resources:
        - name: nvidia.com/gpu
          replicas: 10
EOF

helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update

helm upgrade --install nvdp nvdp/nvidia-device-plugin \
  --namespace kube-system \
  --version "${NVIDIA_DEVICE_PLUGIN_VERSION}" \
  --set config.name=nvidia-device-plugin \
  --set timeSlicing.enabled=true \
  --set nodeSelector."eks-node"=gpu \
  --force

# -----------------------------------------------------------------------------
# Helm repo setup
# -----------------------------------------------------------------------------
echo "Adding and updating EKS helm repo..."
helm repo add eks https://aws.github.io/eks-charts
helm repo update eks

# -----------------------------------------------------------------------------
# Install/Upgrade AWS Load Balancer Controller
# -----------------------------------------------------------------------------
echo "Installing/Upgrading AWS Load Balancer Controller..."
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName="${CLUSTER_NAME}" \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set securityGroup="${SG_ID}" \
  --wait --timeout 5m

# -----------------------------------------------------------------------------
# Install ExternalDNS
# -----------------------------------------------------------------------------
echo "Installing ExternalDNS..."

# Add the Bitnami repo
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Install ExternalDNS
helm upgrade --install external-dns bitnami/external-dns \
  --namespace external-dns \
  --set provider=aws \
  --set aws.zoneType=public \
  --set aws.region="${AWS_REGION}" \
  --set aws.assumeRoleArn="${EXTERNAL_DNS_ROLE}" \

  --set txtOwnerId="external-dns" \
  --set serviceAccount.create=true \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="${EXTERNAL_DNS_SA_ROLE}" \
  --set sources="{service,ingress,istio-gateway,istio-virtualservice}" \
  --set image.pullSecrets[0].name=dockerhub-regcred \
  --wait

# -----------------------------------------------------------------------------
# Set up ArgoCD
# -----------------------------------------------------------------------------
echo "Setting up ArgoCD..."
if kubectl get namespace argocd &>/dev/null; then
  echo "Namespace argocd already exists."
else
  kubectl create namespace argocd
  kubectl label namespace argocd \
    pod-security.kubernetes.io/audit=restricted \
    pod-security.kubernetes.io/audit-version=latest \
    pod-security.kubernetes.io/warn=restricted \
    pod-security.kubernetes.io/warn-version=latest
fi

echo "Applying ArgoCD manifests..."
kubectl apply -n argocd -f \
  "https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml"

# -----------------------------------------------------------------------------
# Create secret for ArgoCD to access private repo
# -----------------------------------------------------------------------------
echo "Ensuring ArgoCD secret for private repo..."

if kubectl get secret argocd-private-repo -n argocd &>/dev/null; then
  echo "argocd-private-repo secret already exists."
else
  kubectl create secret generic argocd-private-repo \
    --namespace argocd \
    --type=Opaque \
    --from-literal=type=git \
    --from-literal=username="${ARGO_PRIVATE_USERNAME}" \
    --from-literal=password="${ARGO_PRIVATE_PASSWORD}" \
    --dry-run=client -o yaml | \
    kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml | \
    kubectl annotate --local -f - managed-by=argocd.argoproj.io -o yaml | \
    kubectl apply -f -
fi

# -----------------------------------------------------------------------------
# PSS/PSA Configurations for workload namespaces
# -----------------------------------------------------------------------------
echo "Configuring PSS/PSA for workload namespaces..."



# Define label sets for each profile
restricted_labels=(
    "pod-security.kubernetes.io/audit=restricted"
    "pod-security.kubernetes.io/audit-version=latest"
    "pod-security.kubernetes.io/enforce=restricted"
    "pod-security.kubernetes.io/enforce-version=latest"
    "pod-security.kubernetes.io/warn=restricted"
    "pod-security.kubernetes.io/warn-version=latest"
)

baseline_labels=(
    "pod-security.kubernetes.io/audit=baseline"
    "pod-security.kubernetes.io/audit-version=latest"
    "pod-security.kubernetes.io/enforce=baseline"
    "pod-security.kubernetes.io/enforce-version=latest"
    "pod-security.kubernetes.io/warn=baseline"
    "pod-security.kubernetes.io/warn-version=latest"
)

privileged_labels=(
    "pod-security.kubernetes.io/audit-version=latest"
    "pod-security.kubernetes.io/audit=privileged"
    "pod-security.kubernetes.io/warn-version=latest"
    "pod-security.kubernetes.io/warn=privileged"
)

# Function to validate namespace labels
validate_namespace_labels() {
    local namespace=$1
    local profile=$2
    local desired_labels=()
    local current_labels=($(kubectl get namespace "$namespace" -o jsonpath='{.metadata.labels}' |
        jq -r 'to_entries | map(select(.key | startswith("pod-security.kubernetes.io/"))) | .[] | "\(.key)=\(.value)"'))

    # Set desired labels based on profile
    case "$profile" in
        "restricted")
            desired_labels=("${restricted_labels[@]}")
            ;;
        "baseline")
            desired_labels=("${baseline_labels[@]}")
            ;;
        "privileged")
            desired_labels=("${privileged_labels[@]}")
            # For privileged profile, enforce labels should not exist
            for label in "${current_labels[@]}"; do
                if [[ "$label" == pod-security.kubernetes.io/enforce* ]]; then
                    echo "Found enforce label in privileged namespace: $label"
                    return 1
                fi
            done
            ;;
        *)
            echo "No labels defined for profile: $profile"
            return 1
            ;;
    esac

    echo "Current labels:"
    printf '%s\n' "${current_labels[@]}"
    echo "Desired labels:"
    printf '%s\n' "${desired_labels[@]}"

    # Check if all desired labels are present
    for label in "${desired_labels[@]}"; do
        if [[ ! " ${current_labels[*]} " =~ " ${label} " ]]; then
            echo "Missing label: $label"
            return 1
        fi
    done

    # Check if there are any extra labels (except for privileged profile which has special handling)
    if [[ "$profile" != "privileged" ]]; then
        for label in "${current_labels[@]}"; do
            if [[ ! " ${desired_labels[*]} " =~ " ${label} " ]]; then
                echo "Extra label: $label"
                return 1
            fi
        done
    fi

    echo -e "\e[32mLabels validation successful for namespace: $namespace\e[0m"
    return 0
}

# Function to update namespace labels
update_namespace_labels() {
    local namespace=$1
    local profile=$2
    local desired_labels=()

    # Set desired labels based on profile
    case "$profile" in
        "restricted")
            desired_labels=("${restricted_labels[@]}")
            ;;
        "baseline")
            desired_labels=("${baseline_labels[@]}")
            ;;
        "privileged")
            desired_labels=("${privileged_labels[@]}")
            # Remove any enforce labels for privileged profile
            echo "Removing enforce labels for privileged profile..."
            kubectl label namespace "$namespace" "pod-security.kubernetes.io/enforce-" "pod-security.kubernetes.io/enforce-version-" --overwrite
            ;;
        *)
            echo "No labels defined for profile: $profile"
            return 1
            ;;
    esac

    # Get current labels
    local current_labels=($(kubectl get namespace "$namespace" -o jsonpath='{.metadata.labels}' |
        jq -r 'to_entries | map(select(.key | startswith("pod-security.kubernetes.io/"))) | .[] | "\(.key)=\(.value)"'))

    # Add missing labels
    for label in "${desired_labels[@]}"; do
        if [[ ! " ${current_labels[*]} " =~ " ${label} " ]]; then
            echo -e "\e[32m + Adding label: $label\e[0m"
            kubectl label namespace "$namespace" "$label" --overwrite
        fi
    done

    # Remove unwanted labels (except for privileged which is handled separately)
    if [[ "$profile" != "privileged" ]]; then
        for label in "${current_labels[@]}"; do
            if [[ ! " ${desired_labels[*]} " =~ " ${label} " ]]; then
                # Extract just the label name (without the value) for removal
                label_name=$(echo "$label" | cut -d'=' -f1)
                echo -e "\e[31m - Removing label: $label_name\e[0m"
                kubectl label namespace "$namespace" "$label_name-"
            fi
        done
    fi
}

# Process each namespace
for config in "${namespace_configs[@]}"; do
    IFS=':' read -r namespace profile <<< "$config"
    echo "Processing namespace: $namespace (desired profile: $profile)"

    # Validate initial state
    echo "Validating initial state..."
    if validate_namespace_labels "$namespace" "$profile"; then
        echo "Labels already match desired state, skipping updates"
        continue
    fi

    echo "Labels need updating, proceeding with changes..."
    update_namespace_labels "$namespace" "$profile"

    # Validate final state
    echo "Validating final state..."
    if ! validate_namespace_labels "$namespace" "$profile"; then
        echo "Final validation failed"
        exit 1
    fi
done

# -----------------------------------------------------------------------------
# Deploy Keycloak operator
# -----------------------------------------------------------------------------
kubectl apply -f \
  "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloaks.k8s.keycloak.org-v1.yml"
kubectl apply -f \
  "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloakrealmimports.k8s.keycloak.org-v1.yml"


# -----------------------------------------------------------------------------
# Install/Upgrade External Secret Operator
# -----------------------------------------------------------------------------
echo "Install/Upgrade External Secret Operator..."
helm repo add external-secrets https://charts.external-secrets.io

helm upgrade --install external-secrets \
  external-secrets/external-secrets \
  -n kube-system \
  --set installCRDs=true \
  --set serviceAccount.create=false \
  --set serviceAccount.name=external-secrets-operator \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="${EXTERNAL_SECRETS_SA_ROLE}" \
  --wait

cd ../secrets

# Process & apply secretstore.yaml first
envsubst < "secretstore.yaml" > "secretstore_processed.yaml"
kubectl apply -f "secretstore_processed.yaml"

# Convert PURCHASED_PRODUCTS (comma-separated) into an array
IFS=',' read -ra PURCHASED_PRODUCTS_ARRAY <<< "$PRODUCTS_PURCHASED"

# Loop over each purchased product and apply its YAML manifests
for product in "${PURCHASED_PRODUCTS_ARRAY[@]}"; do
  if [ -d "$product" ]; then
    echo "Processing product: $product"
    for file in "$product"/*.yaml; do
      # Handle the case where no *.yaml files exist
      [ -e "$file" ] || continue

      processed_file="${file%.yaml}_processed.yaml"
      envsubst < "$file" > "$processed_file"
      kubectl apply -f "$processed_file"

      echo "  Applied: $file"
    done
  else
    echo "WARNING: Directory '$product' does not exist in 'secrets' -- skipping."
  fi
done
