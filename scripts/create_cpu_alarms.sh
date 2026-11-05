#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_INSTANCE_ID:?Set TARGET_INSTANCE_ID to the QuickCart EC2 instance ID}"
: "${SNS_TOPIC_ARN:?Set SNS_TOPIC_ARN to an SNS topic with confirmed email/SMS subscriptions}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
# Use 300 seconds with default EC2 monitoring; use 60 only when detailed monitoring is enabled.
CPU_ALARM_PERIOD="${CPU_ALARM_PERIOD:-300}"
if [[ "$CPU_ALARM_PERIOD" != "60" && "$CPU_ALARM_PERIOD" != "300" ]]; then
  echo "CPU_ALARM_PERIOD must be 60 or 300 seconds." >&2
  exit 2
fi

common=(
  --namespace AWS/EC2
  --metric-name CPUUtilization
  --dimensions "Name=InstanceId,Value=${TARGET_INSTANCE_ID}"
  --statistic Average
  --period "$CPU_ALARM_PERIOD"
  --treat-missing-data notBreaching
  --alarm-actions "$SNS_TOPIC_ARN"
  --ok-actions "$SNS_TOPIC_ARN"
  --region "$AWS_REGION"
)

echo "Creating/updating CloudPilot scale-up alarm for ${TARGET_INSTANCE_ID} in ${AWS_REGION}..."
aws cloudwatch put-metric-alarm \
  --alarm-name "CloudPilot-${TARGET_INSTANCE_ID}-CPU-ScaleUp-70" \
  --alarm-description "CloudPilot notification: CPUUtilization >= 70% for 2 of 2 evaluation periods. Review the CloudPilot recommendation; no automatic resize is performed by this alarm." \
  "${common[@]}" \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --threshold 70 \
  --evaluation-periods 2 \
  --datapoints-to-alarm 2

echo "Creating/updating CloudPilot scale-down alarm for ${TARGET_INSTANCE_ID} in ${AWS_REGION}..."
aws cloudwatch put-metric-alarm \
  --alarm-name "CloudPilot-${TARGET_INSTANCE_ID}-CPU-ScaleDown-25" \
  --alarm-description "CloudPilot notification: CPUUtilization <= 25% for 3 of 3 evaluation periods. Review the CloudPilot recommendation; no automatic resize is performed by this alarm." \
  "${common[@]}" \
  --comparison-operator LessThanOrEqualToThreshold \
  --threshold 25 \
  --evaluation-periods 3 \
  --datapoints-to-alarm 3

echo "Done. Verify alarm states with: aws cloudwatch describe-alarms --alarm-name-prefix CloudPilot-${TARGET_INSTANCE_ID}-CPU --region ${AWS_REGION}"
echo "The SNS topic must already have confirmed subscriptions. These alarms send state-change and recovery notifications; they do not execute EC2 resize actions."
