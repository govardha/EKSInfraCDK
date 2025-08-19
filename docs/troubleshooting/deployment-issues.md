# EKS Deployment Troubleshooting Guide

This guide provides comprehensive troubleshooting steps for common issues encountered during EKS cluster deployment using the CDK pipeline and eksctl.

## Quick Diagnostic Commands

### Pipeline Status
```bash
# Check pipeline execution status
aws codepipeline get-pipeline-execution \
  --pipeline-name "${RESOURCE_PREFIX}-infra-pipeline" \
  --pipeline-execution-id "${EXECUTION_ID}"

# List recent executions
aws codepipeline list-pipeline-executions \
  --pipeline-name "${RESOURCE_PREFIX}-infra-pipeline" \
  --max-items 10
```

### CodeBuild Logs
```bash
# Get build logs
aws logs get-log-events \
  --log-group-name "/aws/codebuild/${RESOURCE_PREFIX}-deploy-eks-cluster" \
  --log-stream-name "${BUILD_ID}" \
  --start-time 1640995200000

# Tail live logs
aws logs tail "/aws/codebuild/${RESOURCE_PREFIX}-deploy-eks-cluster" --follow
```

### Cluster Health
```bash
# Basic cluster info
aws eks describe-cluster --name "${CLUSTER_NAME}"

# Node status
kubectl get nodes -o wide

# Pod status across all namespaces
kubectl get pods -A

# Check for failed pods
kubectl get pods -A --field-selector=status.phase=Failed
```

## Wave 1: EKS Cluster Deployment Issues

### Issue: Pipeline Fails During eksctl Execution

#### Symptoms
```
Error: creating CloudFormation stack "eksctl-cluster-name-cluster": 
AlreadyExistsException: Stack [eksctl-cluster-name-cluster] already exists
```

#### Root Causes
1. Previous deployment left CloudFormation stack
2. Cluster exists but eksctl thinks it doesn't
3. Permission issues with CloudFormation

#### Resolution Steps

1. **Check Existing Resources**
   ```bash
   # Check if cluster exists
   aws eks describe-cluster --name "${CLUSTER_NAME}" --region "${AWS_REGION}"
   
   # Check CloudFormation stacks
   aws cloudformation describe-stacks \
     --stack-name "eksctl-${CLUSTER_NAME}-cluster" \
     --region "${AWS_REGION}"
   ```

2. **Clean Up Orphaned Resources**
   ```bash
   # Delete CloudFormation stack if cluster doesn't exist
   aws cloudformation delete-stack \
     --stack-name "eksctl-${CLUSTER_NAME}-cluster" \
     --region "${AWS_REGION}"
   
   # Wait for deletion
   aws cloudformation wait stack-delete-complete \
     --stack-name "eksctl-${CLUSTER_NAME}-cluster" \
     --region "${AWS_REGION}"
   ```

3. **Force Clean Deployment**
   ```bash
   # If cluster exists but needs recreation
   eksctl delete cluster --name="${CLUSTER_NAME}" --region="${AWS_REGION}"
   
   # Clean up Karpenter resources
   aws cloudformation delete-stack \
     --stack-name "Karpenter-${CLUSTER_NAME}" \
     --region "${AWS_REGION}"
   ```

### Issue: VPC/Subnet Discovery Failures

#### Symptoms
```
Error: VPC_ID is empty or None
Error: Required subnets not found
```

#### Root Causes
1. VPC stack not deployed or failed
2. Incorrect resource naming/tagging
3. Wrong AWS region or account

#### Resolution Steps

1. **Verify VPC Deployment**
   ```bash
   # Check VPC stack status
   aws cloudformation describe-stacks \
     --stack-name "${RESOURCE_PREFIX}-vpc" \
     --region "${AWS_REGION}"
   
   # Check VPC exports
   aws cloudformation list-exports \
     --region "${AWS_REGION}" | grep "${RESOURCE_PREFIX}"
   ```

2. **Manual VPC Discovery**
   ```bash
   # Find VPC by tag
   aws ec2 describe-vpcs \
     --filters "Name=tag:Name,Values=${RESOURCE_PREFIX}-vpc" \
     --query 'Vpcs[*].[VpcId,Tags]' \
     --output table
   
   # Find private subnets
   aws ec2 describe-subnets \
     --filters "Name=tag:Name,Values=*PrivateSubnet*" \
     --query 'Subnets[*].[SubnetId,AvailabilityZone,Tags]' \
     --output table
   ```

3. **Fix Resource Tags**
   ```bash
   # Add missing tags to VPC
   aws ec2 create-tags \
     --resources "${VPC_ID}" \
     --tags Key=Name,Value="${RESOURCE_PREFIX}-vpc"
   
   # Tag subnets for Karpenter discovery
   aws ec2 create-tags \
     --resources "${SUBNET_ID}" \
     --tags Key="karpenter.sh/discovery",Value="${CLUSTER_NAME}"
   ```

### Issue: IAM Permission Errors

#### Symptoms
```
AccessDenied: User: arn:aws:sts::123456789012:assumed-role/codebuild-role 
is not authorized to perform: eks:CreateCluster
```

#### Root Causes
1. CodeBuild role lacks necessary permissions
2. Cross-account role assumption failing
3. Service-linked roles missing

#### Resolution Steps

1. **Verify Role Permissions**
   ```bash
   # Check current role
   aws sts get-caller-identity
   
   # Test specific permissions
   aws eks list-clusters --region "${AWS_REGION}"
   aws ec2 describe-vpcs --region "${AWS_REGION}"
   ```

2. **Fix IAM Policies**
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "eks:*",
           "ec2:*",
           "iam:*",
           "cloudformation:*",
           "autoscaling:*"
         ],
         "Resource": "*"
       }
     ]
   }
   ```

3. **Create Service-Linked Roles**
   ```bash
   # Create EKS service-linked role
   aws iam create-service-linked-role \
     --aws-service-name eks.amazonaws.com
   
   # Create Auto Scaling service-linked role
   aws iam create-service-linked-role \
     --aws-service-name autoscaling.amazonaws.com
   ```

## Wave 2: Cluster Configuration Issues

### Issue: Karpenter Installation Fails

#### Symptoms
```
Error: failed to install karpenter: context deadline exceeded
helm: release karpenter failed
```

#### Root Causes
1. OIDC provider not configured
2. Karpenter CloudFormation stack missing
3. Network connectivity issues
4. Insufficient cluster resources

#### Resolution Steps

1. **Verify OIDC Configuration**
   ```bash
   # Check OIDC provider
   aws eks describe-cluster --name "${CLUSTER_NAME}" \
     --query 'cluster.identity.oidc.issuer'
   
   # List OIDC providers
   aws iam list-open-id-connect-providers
   ```

2. **Check Karpenter Prerequisites**
   ```bash
   # Verify CloudFormation stack
   aws cloudformation describe-stacks \
     --stack-name "Karpenter-${CLUSTER_NAME}"
   
   # Check IAM roles
   aws iam get-role --role-name "KarpenterControllerRole-${CLUSTER_NAME}"
   aws iam get-role --role-name "KarpenterNodeRole-${CLUSTER_NAME}"
   ```

3. **Manual Karpenter Installation**
   ```bash
   # Set required environment variables
   export KARPENTER_VERSION="1.2.1"
   export CLUSTER_NAME="your-cluster-name"
   
   # Install with debugging
   helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter \
     --version "${KARPENTER_VERSION}" \
     --namespace kube-system \
     --set settings.clusterName="${CLUSTER_NAME}" \
     --set settings.interruptionQueue="${CLUSTER_NAME}" \
     --debug \
     --wait \
     --timeout 10m
   ```

### Issue: AWS Load Balancer Controller Fails

#### Symptoms
```
Error: failed to create webhook configuration: 
unable to recognize webhook configuration
```

#### Root Causes
1. Service account not created
2. IRSA role missing or misconfigured
3. Webhook certificate issues
4. Network policy blocking webhook

#### Resolution Steps

1. **Verify Service Account**
   ```bash
   # Check service account
   kubectl get serviceaccount aws-load-balancer-controller -n kube-system
   
   # Check annotations
   kubectl describe serviceaccount aws-load-balancer-controller -n kube-system
   ```

2. **Check IRSA Configuration**
   ```bash
   # Verify role annotation
   kubectl get serviceaccount aws-load-balancer-controller -n kube-system \
     -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}'
   
   # Test role assumption
   aws sts assume-role-with-web-identity \
     --role-arn "${ROLE_ARN}" \
     --role-session-name test \
     --web-identity-token "$(cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token)"
   ```

3. **Reinstall with Debug**
   ```bash
   # Remove existing installation
   helm uninstall aws-load-balancer-controller -n kube-system
   
   # Clean up webhook configurations
   kubectl delete validatingwebhookconfiguration aws-load-balancer-webhook
   kubectl delete mutatingwebhookconfiguration aws-load-balancer-webhook
   
   # Reinstall
   helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
     -n kube-system \
     --set clusterName="${CLUSTER_NAME}" \
     --set serviceAccount.create=false \
     --set serviceAccount.name=aws-load-balancer-controller \
     --debug
   ```

### Issue: ExternalDNS Permission Errors

#### Symptoms
```
Error: failed to assume role for cross-account access
AccessDenied: cannot assume role
```

#### Root Causes
1. Cross-account trust relationship missing
2. DNS validation role not created
3. Route53 permissions insufficient

#### Resolution Steps

1. **Verify Cross-Account Setup**
   ```bash
   # Check if DNS role exists in deployment account
   aws iam get-role --role-name "${RESOURCE_PREFIX}-externaldns-role"
   
   # Check trust policy
   aws iam get-role --role-name "${RESOURCE_PREFIX}-externaldns-role" \
     --query 'Role.AssumeRolePolicyDocument'
   ```

2. **Test Role Assumption**
   ```bash
   # Test from target account
   aws sts assume-role \
     --role-arn "arn:aws:iam::${DEPLOYMENT_ACCOUNT}:role/${RESOURCE_PREFIX}-externaldns-role" \
     --role-session-name test-session
   ```

3. **Fix Trust Relationships**
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "AWS": "arn:aws:iam::TARGET_ACCOUNT:role/RESOURCE_PREFIX-externaldns-sa-role"
         },
         "Action": "sts:AssumeRole"
       }
     ]
   }
   ```

## Wave 3: Application Deployment Issues

### Issue: ArgoCD Applications Not Syncing

#### Symptoms
```
Application health status: Degraded
Sync status: OutOfSync
Unable to connect to repository
```

#### Root Causes
1. Git repository credentials missing
2. Branch or path not found
3. Manifest syntax errors
4. Resource conflicts

#### Resolution Steps

1. **Check ArgoCD Status**
   ```bash
   # Get ArgoCD admin password
   kubectl get secret argocd-initial-admin-secret -n argocd \
     -o jsonpath="{.data.password}" | base64 -d
   
   # Port forward to ArgoCD UI
   kubectl port-forward svc/argocd-server -n argocd 8080:443
   ```

2. **Verify Repository Access**
   ```bash
   # Check repository secret
   kubectl get secret argocd-private-repo -n argocd -o yaml
   
   # Test Git access
   git clone https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}.git
   ```

3. **Debug Application Configuration**
   ```bash
   # Check application status
   kubectl get applications -n argocd
   
   # Describe specific application
   kubectl describe application istio-app -n argocd
   
   # Check application logs
   kubectl logs -n argocd deployment/argocd-application-controller
   ```

### Issue: Istio Gateway Not Accessible

#### Symptoms
```
Error: failed to connect to application endpoints
LoadBalancer stuck in pending state
SSL certificate errors
```

#### Root Causes
1. Network Load Balancer provisioning failed
2. SSL certificate not ready
3. Security group blocking traffic
4. DNS records not created

#### Resolution Steps

1. **Check Load Balancer Status**
   ```bash
   # Get load balancer details
   kubectl get svc -n istio-system istio-gateway
   
   # Check AWS load balancer
   aws elbv2 describe-load-balancers \
     --names "${RESOURCE_PREFIX}-nlb"
   ```

2. **Verify SSL Certificate**
   ```bash
   # Check certificate status
   aws acm describe-certificate \
     --certificate-arn "${CERTIFICATE_ARN}"
   
   # Check validation records
   aws route53 list-resource-record-sets \
     --hosted-zone-id "${HOSTED_ZONE_ID}"
   ```

3. **Debug DNS Resolution**
   ```bash
   # Test DNS resolution
   nslookup "${APP_DOMAIN}"
   
   # Check ExternalDNS logs
   kubectl logs -n external-dns deployment/external-dns
   ```

### Issue: Pod Security Policy Violations

#### Symptoms
```
Error: pods is forbidden: violates PodSecurity "restricted:latest"
unable to validate against any security policy
```

#### Root Causes
1. Pod Security Standards too restrictive
2. Container security context missing
3. Privileged containers in restricted namespaces

#### Resolution Steps

1. **Check Namespace Labels**
   ```bash
   # View current security labels
   kubectl get namespace "${NAMESPACE}" -o yaml | grep pod-security
   
   # Check pod security violations
   kubectl get events -n "${NAMESPACE}" | grep -i violat
   ```

2. **Update Security Context**
   ```yaml
   # Add to pod spec
   securityContext:
     runAsNonRoot: true
     runAsUser: 1000
     fsGroup: 2000
     seccompProfile:
       type: RuntimeDefault
   containers:
   - name: app
     securityContext:
       allowPrivilegeEscalation: false
       capabilities:
         drop:
         - ALL
       readOnlyRootFilesystem: true
   ```

3. **Adjust Namespace Security Level**
   ```bash
   # Change to baseline if needed
   kubectl label namespace "${NAMESPACE}" \
     pod-security.kubernetes.io/enforce=baseline \
     --overwrite
   ```

## Performance and Resource Issues

### Issue: Node Provisioning Too Slow

#### Symptoms
```
Pods stuck in Pending state for extended time
Karpenter not provisioning nodes
Instance types not available
```

#### Root Causes
1. Instance type constraints too restrictive
2. Availability zone capacity issues
3. Karpenter configuration problems
4. IAM permission delays

#### Resolution Steps

1. **Check Karpenter Logs**
   ```bash
   kubectl logs -n kube-system deployment/karpenter
   ```

2. **Verify NodePool Configuration**
   ```bash
   kubectl describe nodepool default
   kubectl describe ec2nodeclass default
   ```

3. **Expand Instance Type Options**
   ```yaml
   requirements:
     - key: karpenter.k8s.aws/instance-category
       operator: In
       values: ["c", "m", "r", "t3"]  # Add more categories
     
     - key: karpenter.k8s.aws/instance-size
       operator: In
       values: ["large", "xlarge", "2xlarge", "4xlarge"]  # More sizes
   ```

### Issue: High Resource Consumption

#### Symptoms
```
Nodes running out of CPU/memory
Frequent pod evictions
Cluster autoscaling issues
```

#### Resolution Steps

1. **Analyze Resource Usage**
   ```bash
   # Check node resource usage
   kubectl top nodes
   
   # Check pod resource usage
   kubectl top pods -A
   
   # Find resource-hungry pods
   kubectl get pods -A -o custom-columns=NAME:.metadata.name,NAMESPACE:.metadata.namespace,CPU:.spec.containers[*].resources.requests.cpu,MEMORY:.spec.containers[*].resources.requests.memory
   ```

2. **Implement Resource Quotas**
   ```yaml
   apiVersion: v1
   kind: ResourceQuota
   metadata:
     name: compute-quota
     namespace: default
   spec:
     hard:
       requests.cpu: "4"
       requests.memory: 8Gi
       limits.cpu: "8"
       limits.memory: 16Gi
   ```

## Monitoring and Alerting Setup

### CloudWatch Dashboards

Create monitoring dashboards for key metrics:

```bash
# EKS cluster metrics
aws cloudwatch put-dashboard \
  --dashboard-name "${CLUSTER_NAME}-overview" \
  --dashboard-body file://dashboard-config.json
```

### Log Analysis

Set up log aggregation and analysis:

```bash
# Search for errors in cluster logs
aws logs filter-log-events \
  --log-group-name "/aws/eks/${CLUSTER_NAME}/cluster" \
  --filter-pattern "ERROR" \
  --start-time $(date -d '1 hour ago' +%s)000

# Search CodeBuild logs for failures
aws logs filter-log-events \
  --log-group-name "/aws/codebuild/${PROJECT_NAME}" \
  --filter-pattern "FAILED|ERROR" \
  --start-time $(date -d '1 hour ago' +%s)000
```

### Automated Health Checks

Implement automated health monitoring:

```bash
#!/bin/bash
# health-check.sh
check_cluster_health() {
  # Test cluster connectivity
  kubectl cluster-info || return 1
  
  # Check critical pods
  kubectl get pods -n kube-system | grep -E "(coredns|aws-load-balancer-controller|karpenter)" | grep -v Running && return 1
  
  # Test application endpoints
  curl -f "https://${APP_DOMAIN}/health" || return 1
  
  return 0
}

# Run health check
if check_cluster_health; then
  echo "Cluster health check passed"
else
  echo "Cluster health check failed - alerting team"
  # Send alert via SNS, Slack, etc.
fi
```

## Recovery Procedures

### Complete Environment Recovery

If the entire environment needs to be rebuilt:

```bash
#!/bin/bash
# disaster-recovery.sh

echo "Starting disaster recovery..."

# 1. Clean up existing resources
eksctl delete cluster --name="${CLUSTER_NAME}" --wait
aws cloudformation delete-stack --stack-name "Karpenter-${CLUSTER_NAME}"

# 2. Trigger pipeline rebuild
aws codepipeline start-pipeline-execution \
  --name "${RESOURCE_PREFIX}-infra-pipeline"

# 3. Monitor pipeline progress
aws codepipeline get-pipeline-execution \
  --pipeline-name "${RESOURCE_PREFIX}-infra-pipeline" \
  --pipeline-execution-id "${EXECUTION_ID}"

echo "Disaster recovery initiated"
```

### Data Backup and Restore

Ensure critical data is backed up:

```bash
# Backup Kubernetes resources
kubectl get all,secrets,configmaps -o yaml > cluster-backup.yaml

# Backup persistent volumes
kubectl get pv,pvc -o yaml > storage-backup.yaml

# Backup ArgoCD applications
kubectl get applications -n argocd -o yaml > argocd-backup.yaml
```

This troubleshooting guide provides comprehensive solutions for the most common issues encountered during EKS deployment. Always start with the quick diagnostic commands to identify the problem area, then follow the specific resolution steps for your issue.
