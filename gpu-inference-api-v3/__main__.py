"""GPU-Powered AI Inference API on AWS ECS with GPU instances

This Pulumi program creates a production-ready GPU inference API infrastructure:
- VPC with public/private subnets across 2 availability zones
- ECS cluster with GPU instances (g4dn.xlarge)
- Application Load Balancer for traffic distribution
- S3 bucket for ML model storage with encryption and versioning
- CloudWatch monitoring with alarms
- Auto-scaling based on CPU/memory metrics
- Security best practices with least-privilege IAM roles
"""

import base64
import json
import pulumi
import pulumi_aws as aws

# Get current AWS region and availability zones
current = aws.get_region()
available_zones = aws.get_availability_zones(state="available")

# Configuration
config = pulumi.Config()
environment = pulumi.get_stack()

# Tags for all resources
tags = {
    "Environment": environment,
    "Project": "gpu-inference-api-v3",
    "ManagedBy": "Pulumi",
}

# ============================================================================
# VPC and Networking
# ============================================================================

# Create VPC with DNS support for service discovery
vpc = aws.ec2.Vpc(
    "gpu-inference-vpc",
    cidr_block="10.0.0.0/16",
    enable_dns_hostnames=True,
    enable_dns_support=True,
    tags={**tags, "Name": f"gpu-inference-vpc-{environment}"},
)

# Create Internet Gateway for public subnet connectivity
igw = aws.ec2.InternetGateway(
    "gpu-inference-igw",
    vpc_id=vpc.id,
    tags={**tags, "Name": f"gpu-inference-igw-{environment}"},
)

# Create public subnets in two AZs (for ALB and NAT Gateway)
public_subnet_1 = aws.ec2.Subnet(
    "gpu-inference-public-subnet-1",
    vpc_id=vpc.id,
    cidr_block="10.0.1.0/24",
    availability_zone=available_zones.names[0],
    map_public_ip_on_launch=True,
    tags={**tags, "Name": f"gpu-inference-public-1-{environment}"},
)

public_subnet_2 = aws.ec2.Subnet(
    "gpu-inference-public-subnet-2",
    vpc_id=vpc.id,
    cidr_block="10.0.2.0/24",
    availability_zone=available_zones.names[1],
    map_public_ip_on_launch=True,
    tags={**tags, "Name": f"gpu-inference-public-2-{environment}"},
)

# Create private subnets in two AZs (for ECS instances)
private_subnet_1 = aws.ec2.Subnet(
    "gpu-inference-private-subnet-1",
    vpc_id=vpc.id,
    cidr_block="10.0.11.0/24",
    availability_zone=available_zones.names[0],
    tags={**tags, "Name": f"gpu-inference-private-1-{environment}"},
)

private_subnet_2 = aws.ec2.Subnet(
    "gpu-inference-private-subnet-2",
    vpc_id=vpc.id,
    cidr_block="10.0.12.0/24",
    availability_zone=available_zones.names[1],
    tags={**tags, "Name": f"gpu-inference-private-2-{environment}"},
)

# Allocate Elastic IP for NAT Gateway
nat_eip = aws.ec2.Eip(
    "gpu-inference-nat-eip",
    domain="vpc",
    tags={**tags, "Name": f"gpu-inference-nat-eip-{environment}"},
)

# Create NAT Gateway in public subnet for private subnet internet access
nat_gateway = aws.ec2.NatGateway(
    "gpu-inference-nat-gateway",
    allocation_id=nat_eip.id,
    subnet_id=public_subnet_1.id,
    tags={**tags, "Name": f"gpu-inference-nat-{environment}"},
)

# Create public route table with internet gateway route
public_route_table = aws.ec2.RouteTable(
    "gpu-inference-public-rt",
    vpc_id=vpc.id,
    routes=[
        aws.ec2.RouteTableRouteArgs(
            cidr_block="0.0.0.0/0",
            gateway_id=igw.id,
        )
    ],
    tags={**tags, "Name": f"gpu-inference-public-rt-{environment}"},
)

# Associate public subnets with public route table
public_rt_assoc_1 = aws.ec2.RouteTableAssociation(
    "gpu-inference-public-rt-assoc-1",
    subnet_id=public_subnet_1.id,
    route_table_id=public_route_table.id,
)

public_rt_assoc_2 = aws.ec2.RouteTableAssociation(
    "gpu-inference-public-rt-assoc-2",
    subnet_id=public_subnet_2.id,
    route_table_id=public_route_table.id,
)

# Create private route table with NAT gateway route
private_route_table = aws.ec2.RouteTable(
    "gpu-inference-private-rt",
    vpc_id=vpc.id,
    routes=[
        aws.ec2.RouteTableRouteArgs(
            cidr_block="0.0.0.0/0",
            nat_gateway_id=nat_gateway.id,
        )
    ],
    tags={**tags, "Name": f"gpu-inference-private-rt-{environment}"},
)

# Associate private subnets with private route table
private_rt_assoc_1 = aws.ec2.RouteTableAssociation(
    "gpu-inference-private-rt-assoc-1",
    subnet_id=private_subnet_1.id,
    route_table_id=private_route_table.id,
)

private_rt_assoc_2 = aws.ec2.RouteTableAssociation(
    "gpu-inference-private-rt-assoc-2",
    subnet_id=private_subnet_2.id,
    route_table_id=private_route_table.id,
)

# ============================================================================
# Security Groups
# ============================================================================

# Security group for Application Load Balancer
alb_security_group = aws.ec2.SecurityGroup(
    "gpu-inference-alb-sg",
    vpc_id=vpc.id,
    description="Security group for GPU inference API load balancer",
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=80,
            to_port=80,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow HTTP from anywhere",
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=443,
            to_port=443,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow HTTPS from anywhere",
        ),
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all outbound traffic",
        )
    ],
    tags={**tags, "Name": f"gpu-inference-alb-sg-{environment}"},
)

# Security group for ECS instances with least-privilege access
ecs_security_group = aws.ec2.SecurityGroup(
    "gpu-inference-ecs-sg",
    vpc_id=vpc.id,
    description="Security group for GPU inference ECS instances",
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=8080,
            to_port=8080,
            security_groups=[alb_security_group.id],
            description="Allow traffic from ALB only",
        ),
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all outbound traffic",
        )
    ],
    tags={**tags, "Name": f"gpu-inference-ecs-sg-{environment}"},
)

# ============================================================================
# S3 Bucket for ML Models
# ============================================================================

# Create S3 bucket for storing ML models
model_bucket = aws.s3.BucketV2(
    "gpu-inference-models",
    tags={**tags, "Name": f"gpu-inference-models-{environment}"},
)

# Enable versioning on the bucket for model version control
model_bucket_versioning = aws.s3.BucketVersioningV2(
    "gpu-inference-models-versioning",
    bucket=model_bucket.id,
    versioning_configuration=aws.s3.BucketVersioningV2VersioningConfigurationArgs(
        status="Enabled",
    ),
)

# Enable server-side encryption with AES256
model_bucket_encryption = aws.s3.BucketServerSideEncryptionConfigurationV2(
    "gpu-inference-models-encryption",
    bucket=model_bucket.id,
    rules=[
        aws.s3.BucketServerSideEncryptionConfigurationV2RuleArgs(
            apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationV2RuleApplyServerSideEncryptionByDefaultArgs(
                sse_algorithm="AES256",
            ),
            bucket_key_enabled=True,
        )
    ],
)

# Block all public access to the bucket
model_bucket_public_access_block = aws.s3.BucketPublicAccessBlock(
    "gpu-inference-models-public-access-block",
    bucket=model_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

# Enable lifecycle policy to manage old model versions
model_bucket_lifecycle = aws.s3.BucketLifecycleConfigurationV2(
    "gpu-inference-models-lifecycle",
    bucket=model_bucket.id,
    rules=[
        aws.s3.BucketLifecycleConfigurationV2RuleArgs(
            id="delete-old-versions",
            status="Enabled",
            noncurrent_version_expiration=aws.s3.BucketLifecycleConfigurationV2RuleNoncurrentVersionExpirationArgs(
                noncurrent_days=90,
            ),
        )
    ],
)

# ============================================================================
# IAM Roles and Policies
# ============================================================================

# IAM role for ECS task execution (used by ECS agent)
task_execution_role = aws.iam.Role(
    "gpu-inference-task-execution-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=tags,
)

# Attach AWS managed policy for ECS task execution
task_execution_policy_attachment = aws.iam.RolePolicyAttachment(
    "gpu-inference-task-execution-policy",
    role=task_execution_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

# IAM role for ECS task (used by the application)
task_role = aws.iam.Role(
    "gpu-inference-task-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=tags,
)

# Policy for S3 access to model bucket (read-only)
s3_policy = aws.iam.Policy(
    "gpu-inference-s3-policy",
    policy=model_bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": [arn, f"{arn}/*"],
                    }
                ],
            }
        )
    ),
    tags=tags,
)

# Attach S3 policy to task role
s3_policy_attachment = aws.iam.RolePolicyAttachment(
    "gpu-inference-s3-policy-attachment",
    role=task_role.name,
    policy_arn=s3_policy.arn,
)

# Policy for CloudWatch Logs
cloudwatch_policy = aws.iam.Policy(
    "gpu-inference-cloudwatch-policy",
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogStreams",
                    ],
                    "Resource": "arn:aws:logs:*:*:*",
                }
            ],
        }
    ),
    tags=tags,
)

# Attach CloudWatch policy to task role
cloudwatch_policy_attachment = aws.iam.RolePolicyAttachment(
    "gpu-inference-cloudwatch-policy-attachment",
    role=task_role.name,
    policy_arn=cloudwatch_policy.arn,
)

# IAM role for EC2 instances in ECS cluster
ec2_instance_role = aws.iam.Role(
    "gpu-inference-ec2-instance-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=tags,
)

# Attach AWS managed policy for ECS EC2 instances
ec2_policy_attachment = aws.iam.RolePolicyAttachment(
    "gpu-inference-ec2-policy-attachment",
    role=ec2_instance_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
)

# Attach SSM policy for EC2 instances (for debugging)
ec2_ssm_policy_attachment = aws.iam.RolePolicyAttachment(
    "gpu-inference-ec2-ssm-policy-attachment",
    role=ec2_instance_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)

# Create instance profile for EC2 instances
instance_profile = aws.iam.InstanceProfile(
    "gpu-inference-instance-profile",
    role=ec2_instance_role.name,
    tags=tags,
)

# ============================================================================
# ECS Cluster with GPU Instances
# ============================================================================

# Create ECS cluster with Container Insights enabled
ecs_cluster = aws.ecs.Cluster(
    "gpu-inference-cluster",
    settings=[
        aws.ecs.ClusterSettingArgs(
            name="containerInsights",
            value="enabled",
        )
    ],
    tags={**tags, "Name": f"gpu-inference-cluster-{environment}"},
)

# Get the latest ECS-optimized GPU AMI
ecs_gpu_ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"],
    filters=[
        aws.ec2.GetAmiFilterArgs(
            name="name",
            values=["amzn2-ami-ecs-gpu-hvm-*-x86_64-ebs"],
        ),
        aws.ec2.GetAmiFilterArgs(
            name="virtualization-type",
            values=["hvm"],
        ),
    ],
)

# User data script for ECS instances with GPU support
user_data = pulumi.Output.all(ecs_cluster.name).apply(
    lambda args: f"""#!/bin/bash
echo ECS_CLUSTER={args[0]} >> /etc/ecs/ecs.config
echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_TASK_IAM_ROLE_NETWORK_HOST=true >> /etc/ecs/ecs.config
"""
)

# Launch template for GPU instances
launch_template = aws.ec2.LaunchTemplate(
    "gpu-inference-launch-template",
    image_id=ecs_gpu_ami.id,
    instance_type="g4dn.xlarge",  # 1 GPU (NVIDIA T4), 4 vCPUs, 16 GB RAM
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        arn=instance_profile.arn,
    ),
    vpc_security_group_ids=[ecs_security_group.id],
    user_data=user_data.apply(lambda ud: base64.b64encode(ud.encode()).decode()),
    block_device_mappings=[
        aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
            device_name="/dev/xvda",
            ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
                volume_size=100,  # Larger volume for models and container images
                volume_type="gp3",
                delete_on_termination="true",
                encrypted="true",
            ),
        )
    ],
    monitoring=aws.ec2.LaunchTemplateMonitoringArgs(
        enabled=True,
    ),
    tag_specifications=[
        aws.ec2.LaunchTemplateTagSpecificationArgs(
            resource_type="instance",
            tags={**tags, "Name": f"gpu-inference-instance-{environment}"},
        ),
        aws.ec2.LaunchTemplateTagSpecificationArgs(
            resource_type="volume",
            tags={**tags, "Name": f"gpu-inference-volume-{environment}"},
        ),
    ],
    tags={**tags, "Name": f"gpu-inference-launch-template-{environment}"},
)

# Auto Scaling Group for ECS instances
asg = aws.autoscaling.Group(
    "gpu-inference-asg",
    vpc_zone_identifiers=[private_subnet_1.id, private_subnet_2.id],
    desired_capacity=1,
    min_size=1,
    max_size=3,
    health_check_type="EC2",
    health_check_grace_period=300,
    launch_template=aws.autoscaling.GroupLaunchTemplateArgs(
        id=launch_template.id,
        version="$Latest",
    ),
    tags=[
        aws.autoscaling.GroupTagArgs(
            key="Name",
            value=f"gpu-inference-asg-{environment}",
            propagate_at_launch=True,
        ),
        aws.autoscaling.GroupTagArgs(
            key="AmazonECSManaged",
            value="true",
            propagate_at_launch=True,
        ),
    ],
)

# ECS Capacity Provider for managed scaling
capacity_provider = aws.ecs.CapacityProvider(
    "gpu-inference-capacity-provider",
    auto_scaling_group_provider=aws.ecs.CapacityProviderAutoScalingGroupProviderArgs(
        auto_scaling_group_arn=asg.arn,
        managed_scaling=aws.ecs.CapacityProviderAutoScalingGroupProviderManagedScalingArgs(
            status="ENABLED",
            target_capacity=80,
            minimum_scaling_step_size=1,
            maximum_scaling_step_size=1,
        ),
        managed_termination_protection="DISABLED",
    ),
    tags=tags,
)

# Associate capacity provider with cluster
cluster_capacity_providers = aws.ecs.ClusterCapacityProviders(
    "gpu-inference-cluster-capacity-providers",
    cluster_name=ecs_cluster.name,
    capacity_providers=[capacity_provider.name],
    default_capacity_provider_strategies=[
        aws.ecs.ClusterCapacityProvidersDefaultCapacityProviderStrategyArgs(
            capacity_provider=capacity_provider.name,
            weight=1,
            base=1,
        )
    ],
)

# ============================================================================
# CloudWatch Log Group
# ============================================================================

# Create CloudWatch log group for ECS tasks
log_group = aws.cloudwatch.LogGroup(
    "gpu-inference-log-group",
    retention_in_days=7,
    tags=tags,
)

# ============================================================================
# ECS Task Definition
# ============================================================================

# Task definition for GPU inference API
task_definition = aws.ecs.TaskDefinition(
    "gpu-inference-task",
    family="gpu-inference-api",
    network_mode="bridge",
    requires_compatibilities=["EC2"],
    cpu="2048",  # 2 vCPUs
    memory="8192",  # 8 GB
    execution_role_arn=task_execution_role.arn,
    task_role_arn=task_role.arn,
    container_definitions=pulumi.Output.all(
        log_group.name, model_bucket.id, current.name
    ).apply(
        lambda args: json.dumps(
            [
                {
                    "name": "gpu-inference-api",
                    "image": "public.ecr.aws/docker/library/python:3.11-slim",
                    "cpu": 2048,
                    "memory": 8192,
                    "essential": True,
                    "portMappings": [
                        {"containerPort": 8080, "hostPort": 8080, "protocol": "tcp"}
                    ],
                    "environment": [
                        {"name": "MODEL_BUCKET", "value": args[1]},
                        {"name": "AWS_REGION", "value": args[2]},
                        {"name": "PORT", "value": "8080"},
                    ],
                    "command": [
                        "sh",
                        "-c",
                        'pip install fastapi uvicorn && python -c "from fastapi import FastAPI; import uvicorn; app = FastAPI(); @app.get(\\"/health\\"); def health(): return {\\"status\\": \\"healthy\\"}; uvicorn.run(app, host=\\"0.0.0.0\\", port=8080)"',
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": args[0],
                            "awslogs-region": args[2],
                            "awslogs-stream-prefix": "gpu-inference",
                        },
                    },
                    "resourceRequirements": [{"type": "GPU", "value": "1"}],
                    "healthCheck": {
                        "command": [
                            "CMD-SHELL",
                            "curl -f http://localhost:8080/health || exit 1",
                        ],
                        "interval": 30,
                        "timeout": 5,
                        "retries": 3,
                        "startPeriod": 60,
                    },
                }
            ]
        )
    ),
    tags=tags,
)

# ============================================================================
# Application Load Balancer
# ============================================================================

# Create Application Load Balancer in public subnets
alb = aws.lb.LoadBalancer(
    "gpu-inference-alb",
    load_balancer_type="application",
    subnets=[public_subnet_1.id, public_subnet_2.id],
    security_groups=[alb_security_group.id],
    enable_deletion_protection=False,
    enable_http2=True,
    tags={**tags, "Name": f"gpu-inference-alb-{environment}"},
)

# Create target group for ECS service
target_group = aws.lb.TargetGroup(
    "gpu-inference-tg",
    port=8080,
    protocol="HTTP",
    vpc_id=vpc.id,
    target_type="instance",
    deregistration_delay=30,
    health_check=aws.lb.TargetGroupHealthCheckArgs(
        enabled=True,
        path="/health",
        protocol="HTTP",
        port="8080",
        healthy_threshold=2,
        unhealthy_threshold=3,
        timeout=5,
        interval=30,
        matcher="200-299",
    ),
    tags={**tags, "Name": f"gpu-inference-tg-{environment}"},
)

# Create ALB listener on port 80
alb_listener = aws.lb.Listener(
    "gpu-inference-alb-listener",
    load_balancer_arn=alb.arn,
    port=80,
    protocol="HTTP",
    default_actions=[
        aws.lb.ListenerDefaultActionArgs(
            type="forward",
            target_group_arn=target_group.arn,
        )
    ],
    tags=tags,
)

# ============================================================================
# ECS Service
# ============================================================================

# Create ECS service with load balancer integration
ecs_service = aws.ecs.Service(
    "gpu-inference-service",
    cluster=ecs_cluster.arn,
    task_definition=task_definition.arn,
    desired_count=1,
    launch_type="EC2",
    scheduling_strategy="REPLICA",
    deployment_maximum_percent=200,
    deployment_minimum_healthy_percent=50,
    deployment_circuit_breaker=aws.ecs.ServiceDeploymentCircuitBreakerArgs(
        enable=True,
        rollback=True,
    ),
    load_balancers=[
        aws.ecs.ServiceLoadBalancerArgs(
            target_group_arn=target_group.arn,
            container_name="gpu-inference-api",
            container_port=8080,
        )
    ],
    health_check_grace_period_seconds=60,
    tags=tags,
    opts=pulumi.ResourceOptions(depends_on=[alb_listener]),
)

# ============================================================================
# Auto Scaling
# ============================================================================

# Auto Scaling Target for ECS service
scaling_target = aws.appautoscaling.Target(
    "gpu-inference-scaling-target",
    max_capacity=3,
    min_capacity=1,
    resource_id=pulumi.Output.all(ecs_cluster.name, ecs_service.name).apply(
        lambda args: f"service/{args[0]}/{args[1]}"
    ),
    scalable_dimension="ecs:service:DesiredCount",
    service_namespace="ecs",
)

# Auto Scaling Policy based on CPU utilization
cpu_scaling_policy = aws.appautoscaling.Policy(
    "gpu-inference-cpu-scaling-policy",
    policy_type="TargetTrackingScaling",
    resource_id=scaling_target.resource_id,
    scalable_dimension=scaling_target.scalable_dimension,
    service_namespace=scaling_target.service_namespace,
    target_tracking_scaling_policy_configuration=aws.appautoscaling.PolicyTargetTrackingScalingPolicyConfigurationArgs(
        predefined_metric_specification=aws.appautoscaling.PolicyTargetTrackingScalingPolicyConfigurationPredefinedMetricSpecificationArgs(
            predefined_metric_type="ECSServiceAverageCPUUtilization",
        ),
        target_value=70.0,
        scale_in_cooldown=300,
        scale_out_cooldown=60,
    ),
)

# Auto Scaling Policy based on memory utilization
memory_scaling_policy = aws.appautoscaling.Policy(
    "gpu-inference-memory-scaling-policy",
    policy_type="TargetTrackingScaling",
    resource_id=scaling_target.resource_id,
    scalable_dimension=scaling_target.scalable_dimension,
    service_namespace=scaling_target.service_namespace,
    target_tracking_scaling_policy_configuration=aws.appautoscaling.PolicyTargetTrackingScalingPolicyConfigurationArgs(
        predefined_metric_specification=aws.appautoscaling.PolicyTargetTrackingScalingPolicyConfigurationPredefinedMetricSpecificationArgs(
            predefined_metric_type="ECSServiceAverageMemoryUtilization",
        ),
        target_value=80.0,
        scale_in_cooldown=300,
        scale_out_cooldown=60,
    ),
)

# ============================================================================
# CloudWatch Alarms
# ============================================================================

# Alarm for high CPU utilization
cpu_alarm = aws.cloudwatch.MetricAlarm(
    "gpu-inference-cpu-alarm",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="CPUUtilization",
    namespace="AWS/ECS",
    period=300,
    statistic="Average",
    threshold=80.0,
    alarm_description="Alert when CPU exceeds 80%",
    alarm_actions=[],
    dimensions={
        "ClusterName": ecs_cluster.name,
        "ServiceName": ecs_service.name,
    },
    tags=tags,
)

# Alarm for high memory utilization
memory_alarm = aws.cloudwatch.MetricAlarm(
    "gpu-inference-memory-alarm",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="MemoryUtilization",
    namespace="AWS/ECS",
    period=300,
    statistic="Average",
    threshold=85.0,
    alarm_description="Alert when memory exceeds 85%",
    alarm_actions=[],
    dimensions={
        "ClusterName": ecs_cluster.name,
        "ServiceName": ecs_service.name,
    },
    tags=tags,
)

# Alarm for unhealthy targets
unhealthy_target_alarm = aws.cloudwatch.MetricAlarm(
    "gpu-inference-unhealthy-target-alarm",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="UnHealthyHostCount",
    namespace="AWS/ApplicationELB",
    period=60,
    statistic="Average",
    threshold=0,
    alarm_description="Alert when there are unhealthy targets",
    alarm_actions=[],
    dimensions={
        "TargetGroup": target_group.arn_suffix,
        "LoadBalancer": alb.arn_suffix,
    },
    tags=tags,
)

# Alarm for ALB 5XX errors
alb_5xx_alarm = aws.cloudwatch.MetricAlarm(
    "gpu-inference-alb-5xx-alarm",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="HTTPCode_Target_5XX_Count",
    namespace="AWS/ApplicationELB",
    period=300,
    statistic="Sum",
    threshold=10,
    alarm_description="Alert when ALB returns too many 5XX errors",
    alarm_actions=[],
    dimensions={
        "LoadBalancer": alb.arn_suffix,
    },
    tags=tags,
)

# ============================================================================
# Stack Outputs
# ============================================================================

# Export VPC and networking information
pulumi.export("vpc_id", vpc.id)
pulumi.export("public_subnet_ids", [public_subnet_1.id, public_subnet_2.id])
pulumi.export("private_subnet_ids", [private_subnet_1.id, private_subnet_2.id])

# Export S3 bucket information
pulumi.export("model_bucket_name", model_bucket.id)
pulumi.export("model_bucket_arn", model_bucket.arn)

# Export API endpoint
pulumi.export("api_endpoint", alb.dns_name.apply(lambda dns: f"http://{dns}"))
pulumi.export("alb_dns_name", alb.dns_name)

# Export ECS cluster information
pulumi.export("ecs_cluster_name", ecs_cluster.name)
pulumi.export("ecs_cluster_arn", ecs_cluster.arn)
pulumi.export("ecs_service_name", ecs_service.name)

# Export CloudWatch log group
pulumi.export("log_group_name", log_group.name)

# Export CloudWatch dashboard URL
pulumi.export(
    "cloudwatch_dashboard_url",
    pulumi.Output.all(current.name, ecs_cluster.name).apply(
        lambda args: f"https://console.aws.amazon.com/cloudwatch/home?region={args[0]}#dashboards:name=ECS-{args[1]}"
    ),
)

# Export CloudWatch Logs Insights URL
pulumi.export(
    "cloudwatch_logs_url",
    pulumi.Output.all(current.name, log_group.name).apply(
        lambda args: f"https://console.aws.amazon.com/cloudwatch/home?region={args[0]}#logsV2:log-groups/log-group/{args[1].replace('/', '$252F')}"
    ),
)
