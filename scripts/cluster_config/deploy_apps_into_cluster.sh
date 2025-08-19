#!/bin/bash

set -e

# -----------------------------------------------------------------------------
# Clone the argocd-app-config repository
# -----------------------------------------------------------------------------
echo "Cloning argocd-app-config repo..."
USERNAME="$(aws secretsmanager get-secret-value \
  --query 'SecretString' \
  --output text | jq -r .username)"
PASSWORD="$(aws secretsmanager get-secret-value \
  --query 'SecretString' \
  --output text | jq -r .password)"

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
# Configure kubectl
# -----------------------------------------------------------------------------
echo "Configuring kubectl..."
aws eks update-kubeconfig \
  --name "${CLUSTER_NAME}" \
  --region "${AWS_REGION}"

# -----------------------------------------------------------------------------
# Set values for applications in ArgoCD and create new branch
# -----------------------------------------------------------------------------
export KEYCLOAK_URL="https://idm.${APP_DOMAIN}"
export CERTIFICATE_ARN="$(aws ssm get-parameter \
  --name "${ACM_CERTIFICATE_ARN}" \
  --query Parameter.Value \
  --output text)"
export NLB_NAME="${RESOURCE_PREFIX}-nlb"


TEMPLATE_BRANCH="main"
git checkout "${TEMPLATE_BRANCH}"
git pull


NEW_BRANCH="develop/${RESOURCE_PREFIX}"
git checkout -b "${NEW_BRANCH}"

# -----------------------------------------------------------------------------
# Update application configurations
# -----------------------------------------------------------------------------

# Update all 'applicationset.yaml' files
find . -type f -name 'applicationset.yaml' | while read -r file; do
    echo "Updating targetRevision in ${file}"
    sed -i "s|targetRevision: ${TEMPLATE_BRANCH}|targetRevision: ${NEW_BRANCH}|g" "${file}"

    # Replace environment variable placeholders
    TEMP_FILE="${file}.temp"
    envsubst '${RESOURCE_PREFIX} ${APP_DOMAIN} ${CERTIFICATE_ARN} ${TARGET_ACCOUNT_ID}' < "${file}" > "${TEMP_FILE}"
    mv "${TEMP_FILE}" "${file}"
done

# Process Istio Gateway application
ISTIO_GATEWAY_APP_FILE="istio/istio-app/gateway.yaml"
echo "Processing Istio Gateway application"
sed -i "s|targetRevision: ${TEMPLATE_BRANCH}|targetRevision: ${NEW_BRANCH}|g" "${ISTIO_GATEWAY_APP_FILE}"
TEMP_VALUES="${ISTIO_GATEWAY_APP_FILE}.temp"
envsubst '${RESOURCE_PREFIX} ${APP_DOMAIN} ${CERTIFICATE_ARN}' < "${ISTIO_GATEWAY_APP_FILE}" > "${TEMP_VALUES}"
mv "${TEMP_VALUES}" "${ISTIO_GATEWAY_APP_FILE}"

# Process Istio Gateway values.yaml
ISTIO_VALUES_FILE="istio/istio-gateway/values.yaml"
echo "Processing Istio values.yaml"
TEMP_VALUES="${ISTIO_VALUES_FILE}.temp"
envsubst '${NLB_NAME} ${CERTIFICATE_ARN} ${HOSTNAMES} ${APP_DOMAIN}' < "${ISTIO_VALUES_FILE}" > "${TEMP_VALUES}"
mv "${TEMP_VALUES}" "${ISTIO_VALUES_FILE}"


# -----------------------------------------------------------------------------
# Commit and push changes
# -----------------------------------------------------------------------------
git add .
git commit -m "Update targetRevision to ${NEW_BRANCH} for ${RESOURCE_PREFIX}"
git push --force-with-lease origin "${NEW_BRANCH}"

# -----------------------------------------------------------------------------
# Apply prerequisite services
# -----------------------------------------------------------------------------
echo "Applying prerequisite services.."
kubectl apply -f projects

if kubectl get namespace istio-system &>/dev/null; then
  echo "Namespace istio-system already exists."
else
  kubectl create namespace istio-system
  kubectl label namespace istio-system \
    pod-security.kubernetes.io/audit=baseline \
    pod-security.kubernetes.io/audit-version=latest \
    pod-security.kubernetes.io/warn=baseline \
    pod-security.kubernetes.io/warn-version=latest
fi

# Deploy Istio App
kubectl apply -f ./istio/istio-app/applicationset.yaml

sleep 10  # Replace with a proper wait for the app to be ready

# Deploy Istio Gateway
kubectl apply -f ./istio/istio-app/gateway.yaml



# -----------------------------------------------------------------------------
# Wait until keycloak pod is ready
# -----------------------------------------------------------------------------
# Function to wait for a pod to be ready
wait_for_pod_ready() {
  local pod_name=$1
  local namespace=$2
  local timeout=300

  echo "Waiting for pod ${pod_name} in namespace ${namespace} to appear..."
  local end=$((SECONDS+timeout))

  while ! kubectl get pod ${pod_name} \
    -n ${namespace} \
    >/dev/null 2>&1; do
    [ $SECONDS -ge $end ] && echo "Timed out waiting for pod to appear." && exit 1
    sleep 10
  done

  echo "Pod found. Checking readiness..."
  local end=$((SECONDS+timeout))

  while [ "$(kubectl get pod ${pod_name} \
    -n ${namespace} \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" != "True" ]; do
    [ $SECONDS -ge $end ] && echo "Timed out waiting for pod to become Ready." && exit 1
    sleep 10
  done

  echo "Pod ${pod_name} in namespace ${namespace} is ready."
}


# -----------------------------------------------------------------------------
# Set required environment variables for Keycloak
# -----------------------------------------------------------------------------
export KEYCLOAK_ADMIN_USERNAME="$(aws secretsmanager get-secret-value \
  --secret-id ${KEYCLOAK_ADMIN_CREDENTIALS} \
  --query 'SecretString' \
  --output text | jq -r .username)"
export KEYCLOAK_ADMIN_PASSWORD="$(aws secretsmanager get-secret-value \
  --secret-id ${KEYCLOAK_ADMIN_CREDENTIALS} \
  --query 'SecretString' \
  --output text | jq -r .password)"

# -----------------------------------------------------------------------------
# Wait until NLB is provisioned and execute keycloak-api.py
# -----------------------------------------------------------------------------
while [ "$(aws elbv2 describe-load-balancers \
  --names ${RESOURCE_PREFIX}-nlb \
  --query "LoadBalancers[0].State.Code" \
  --output json)" != "\"active\"" ]; do
    echo "Waiting for NLB to be provisioned..."
    sleep 10
done

echo "Executing keycloak-api.py..."
pip install requests


# -----------------------------------------------------------------------------
# Deploy products purchased apps and dependencies
# -----------------------------------------------------------------------------
echo "Deploying products purchased apps..."

# Convert comma-separated string to array
IFS=',' read -ra PURCHASED_PRODUCTS <<< "$PRODUCTS_PURCHASED"




# -----------------------------------------------------------------------------
# Deploy application sets for all purchased products
# -----------------------------------------------------------------------------
echo "Deploying application sets for all purchased products..."

for product in "${PURCHASED_PRODUCTS[@]}"; do
    appsets_path="${product}/${product}-app/applicationset.yaml"
    if [ -f "$appsets_path" ]; then
        echo "Applying applicationset for purchased product: ${product}"
        kubectl apply -f "$appsets_path"
    else
        echo "Warning: applicationset.yaml not found at ${appsets_path}"
    fi
done

# -----------------------------------------------------------------------------
# Deploy Grafana and Prometheus observability stack
# -----------------------------------------------------------------------------
echo "Deploying Grafana and Prometheus..."

# Install Helm
curl https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 > get_helm.sh
chmod 700 get_helm.sh
./get_helm.sh

# Add the Prometheus community Helm repo
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# Create observability namespace
if kubectl get namespace observability &>/dev/null; then
  echo "Namespace observability already exists."
else
  kubectl create namespace observability
fi

# Install Kube Prometheus Stack Helm chart
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace observability \
  -f observability/prometheus-values.yaml

# Install Loki
helm upgrade --install loki grafana/loki-distributed \
  --namespace observability \
  -f observability/loki-values.yaml
