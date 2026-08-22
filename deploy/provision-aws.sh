#!/bin/bash
# Provisions the EC2 instance this deployment runs on. Run locally, once,
# after `aws configure` (or `aws sso login`) has succeeded. Idempotent-ish:
# re-running reuses the key pair / security group / instance if they already
# exist by name/tag, rather than creating duplicates.
#
#   ./provision-aws.sh
#
set -euo pipefail

REGION="${REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
NAME="${NAME:-sentinel-platform}"
KEY_NAME="${NAME}-key"
SG_NAME="${NAME}-sg"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> confirming AWS auth"
aws sts get-caller-identity --region "$REGION" > /dev/null

MY_IP=$(curl -s https://checkip.amazonaws.com)/32
echo "==> your IP for SSH access: $MY_IP"

echo "==> key pair"
if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" > /dev/null 2>&1; then
  aws ec2 create-key-pair --region "$REGION" --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "$DEPLOY_DIR/$KEY_NAME.pem"
  chmod 400 "$DEPLOY_DIR/$KEY_NAME.pem"
  echo "    created, saved to $DEPLOY_DIR/$KEY_NAME.pem"
else
  echo "    $KEY_NAME already exists, reusing (make sure you still have the .pem)"
fi

echo "==> security group"
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters Name=group-name,Values="$SG_NAME" Name=vpc-id,Values="$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [ "$SG_ID" == "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group --region "$REGION" --group-name "$SG_NAME" \
    --description "Sentinel + flight-recorder demo" --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
    --ip-permissions \
    "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$MY_IP,Description='ssh'}]" \
    "IpProtocol=tcp,FromPort=4000,ToPort=4000,IpRanges=[{CidrIp=0.0.0.0/0,Description='sentinel dashboard'}]" \
    "IpProtocol=tcp,FromPort=8000,ToPort=8000,IpRanges=[{CidrIp=0.0.0.0/0,Description='flight-recorder console'}]"
  echo "    created $SG_ID"
else
  echo "    $SG_ID already exists, reusing"
fi

echo "==> latest Amazon Linux 2023 AMI"
AMI_ID=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameter.Value' --output text)
echo "    $AMI_ID"

echo "==> checking for an existing instance"
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo "None")

if [ "$INSTANCE_ID" == "None" ] || [ -z "$INSTANCE_ID" ]; then
  echo "==> launching instance"
  INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
    --user-data "file://$DEPLOY_DIR/ec2-userdata.sh" \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=20,VolumeType=gp3}' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
  echo "    launched $INSTANCE_ID"
else
  echo "    reusing existing instance $INSTANCE_ID"
fi

echo "==> waiting for instance to be running"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

echo "==> allocating/associating an Elastic IP"
EIP_ALLOC=$(aws ec2 describe-addresses --region "$REGION" \
  --filters "Name=tag:Name,Values=$NAME" \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null || echo "None")
if [ "$EIP_ALLOC" == "None" ] || [ -z "$EIP_ALLOC" ]; then
  EIP_ALLOC=$(aws ec2 allocate-address --region "$REGION" --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'AllocationId' --output text)
fi
aws ec2 associate-address --region "$REGION" --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC" > /dev/null
PUBLIC_IP=$(aws ec2 describe-addresses --region "$REGION" --allocation-ids "$EIP_ALLOC" \
  --query 'Addresses[0].PublicIp' --output text)

echo "############################################################"
echo "Instance:   $INSTANCE_ID"
echo "Public IP:  $PUBLIC_IP  (won't change on stop/start)"
echo ""
echo "Next: wait ~60s for cloud-init, then:"
echo "  ssh -i $DEPLOY_DIR/$KEY_NAME.pem ec2-user@$PUBLIC_IP 'cloud-init status --wait'"
echo "  scp -i $DEPLOY_DIR/$KEY_NAME.pem -r $DEPLOY_DIR ec2-user@$PUBLIC_IP:~/deploy"
echo "  ssh -i $DEPLOY_DIR/$KEY_NAME.pem ec2-user@$PUBLIC_IP \\"
echo "    'REPO_URL=<your-repo-url> sudo -E bash ~/deploy/deploy.sh'"
echo "############################################################"
