"""GPU-Powered AI Inference API on AWS

This Pulumi program creates a production-ready GPU inference API infrastructure including:
- VPC with public/private subnets across multiple availability zones
- ECS cluster with GPU-enabled EC2 instances (g4dn.xlarge)
- Application Load Balancer for traffic distribution
- S3 bucket for ML model storage
- IAM roles and security groups with least-privilege access
- CloudWatch monitoring and logging
"""

import pulumi
import pulumi_aws as aws

# Get current AWS region and availability zones
current = aws.get_region()
available_azs = aws.get_availability_zones(state="available")

# Configuration
config = pulumi.Config()
project_name = pulumi.get_project()
stack_name = pulumi.get_stack()
environment = stack_name

# Use first 2 AZs for high availability
azs = available_azs.names[:2]

# ============================================================================
# VPC and Networking
# ============================================================================

# Create VPC
vpc = aws.ec2.Vpc(
    "gpu-inference-vpc",
    cidr_block="10.0.0.0/16",
    enable_dns_hostnames=True,
    enable_dns_support=True,
    tags={
        "Name": f"{project_name}-{environment}-vpc",
        "Environment": environment,
        "Project": project_name,
    },
)

# Create Internet Gateway
igw = aws.ec2.InternetGateway(
    "gpu-inference-igw",
    vpc_id=vpc.id,
    tags={
        "Name": f"{project_name}-{environment}-igw",
        "Environment": environment,
    },
)

# Create public subnets (one per AZ)
public_subnets = []
for i, az in enumerate(azs):
    subnet = aws.ec2.Subnet(
        f"public-subnet-{i}",
        vpc_id=vpc.id,
        cidr_block=f"10.0.{i}.0/24",
        availability_zone=az,
        map_public_ip_on_launch=True,
        tags={
            "Name": f"{project_name}-{environment}-public-{az}",
            "Environment": environment,
            "Type": "public",
        },
    )
    public_subnets.append(subnet)

# Create private subnets (one per AZ)
private_subnets = []
for i, az in enumerate(azs):
    subnet = aws.ec2.Subnet(
        f"private-subnet-{i}",
        vpc_id=vpc.id,
        cidr_block=f"10.0.{i+10}.0/24",
        availability_zone=az,
        tags={
            "Name": f"{project_name}-{environment}-private-{az}",
            "Environment": environment,
            "Type": "private",
        },
    )
    private_subnets.append(subnet)

# Allocate Elastic IP for NAT Gateway
eip = aws.ec2.Eip(
    "nat-eip",
    domain="vpc",
    tags={
        "Name": f"{project_name}-{environment}-nat-eip",
        "Environment": environment,
    },
)

# Create NAT Gateway in first public subnet
nat_gateway = aws.ec2.NatGateway(
    "nat-gateway",
    allocation_id=eip.id,
    subnet_id=public_subnets[0].id,
    tags={
        "Name": f"{project_name}-{environment}-nat",
        "Environment": environment,
    },
)

# Create public route table
public_route_table = aws.ec2.RouteTable(
    "public-rt",
    vpc_id=vpc.id,
    tags={
        "Name": f"{project_name}-{environment}-public-rt",
        "Environment": environment,
    },
)

# Route public traffic to Internet Gateway
public_route = aws.ec2.Route(
    "public-route",
    route_table_id=public_route_table.id,
    destination_cidr_block="0.0.0.0/0",
    gateway_id=igw.id,
)

# Associate public subnets with public route table
for i, subnet in enumerate(public_subnets):
    aws.ec2.RouteTableAssociation(
        f"public-rta-{i}",
        subnet_id=subnet.id,
        route_table_id=public_route_table.id,
    )

# Create private route table
private_route_table = aws.ec2.RouteTable(
    "private-rt",
    vpc_id=vpc.id,
    tags={
        "Name": f"{project_name}-{environment}-private-rt",
        "Environment": environment,
    },
)

# Route private traffic to NAT Gateway
private_route = aws.ec2.Route(
    "private-route",
    route_table_id=private_route_table.id,
    destination_cidr_block="0.0.0.0/0",
    nat_gateway_id=nat_gateway.id,
)

# Associate private subnets with private route table
for i, subnet in enumerate(private_subnets):
    aws.ec2.RouteTableAssociation(
        f"private-rta-{i}",
        subnet_id=subnet.id,
        route_table_id=private_route_table.id,
    )

# ============================================================================
# S3 Bucket for Model Storage
# ============================================================================

# Create S3 bucket for storing ML models
model_bucket = aws.s3.BucketV2(
    "model-bucket",
    tags={
        "Name": f"{project_name}-{environment}-models",
        "Environment": environment,
        "Purpose": "ml-model-storage",
    },
)

# Enable versioning for model tracking
bucket_versioning = aws.s3.BucketVersioningV2(
    "model-bucket-versioning",
    bucket=model_bucket.id,
    versioning_configuration=aws.s3.BucketVersioningV2VersioningConfigurationArgs(
        status="Enabled",
    ),
)

# Enable server-side encryption
bucket_encryption = aws.s3.BucketServerSideEncryptionConfigurationV2(
    "model-bucket-encryption",
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

# Block public access
bucket_public_access_block = aws.s3.BucketPublicAccessBlock(
    "model-bucket-public-access-block",
    bucket=model_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

# Lifecycle policy to manage old model versions
bucket_lifecycle = aws.s3.BucketLifecycleConfigurationV2(
    "model-bucket-lifecycle",
    bucket=model_bucket.id,
    rules=[
        aws.s3.BucketLifecycleConfigurationV2RuleArgs(
            id="delete-old-versions",
            status="Enabled",
            noncurrent_version_expiration=aws.s3.BucketLifecycleConfigurationV2RuleNoncurrentVersionExpirationArgs(
                noncurrent_days=90,
            ),
        ),
        aws.s3.BucketLifecycleConfigurationV2RuleArgs(
            id="abort-incomplete-uploads",
            status="Enabled",
            abort_incomplete_multipart_upload=aws.s3.BucketLifecycleConfigurationV2RuleAbortIncompleteMultipartUploadArgs(
                days_after_initiation=7,
            ),
        ),
    ],
)

# ============================================================================
# IAM Roles and Policies
# ============================================================================

# ECS Task Execution Role - allows ECS to pull images and write logs
ecs_task_execution_role = aws.iam.Role(
    "ecs-task-execution-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Service": "ecs-tasks.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }]
    }""",
    tags={
        "Name": f"{project_name}-{environment}-ecs-execution-role",
        "Environment": environment,
    },
)

# Attach AWS managed policy for ECS task execution
ecs_task_execution_policy_attachment = aws.iam.RolePolicyAttachment(
    "ecs-task-execution-policy-attachment",
    role=ecs_task_execution_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

# ECS Task Role - allows containers to access AWS services
ecs_task_role = aws.iam.Role(
    "ecs-task-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Service": "ecs-tasks.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }]
    }""",
    tags={
        "Name": f"{project_name}-{environment}-ecs-task-role",
        "Environment": environment,
    },
)

# Policy to allow ECS tasks to access S3 model bucket
s3_access_policy = aws.iam.Policy(
    "s3-model-access-policy",
    policy=model_bucket.arn.apply(
        lambda arn: f"""{{
        "Version": "2012-10-17",
        "Statement": [{{
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "{arn}",
                "{arn}/*"
            ]
        }}]
    }}"""
    ),
)

# Attach S3 access policy to task role
s3_policy_attachment = aws.iam.RolePolicyAttachment(
    "s3-policy-attachment",
    role=ecs_task_role.name,
    policy_arn=s3_access_policy.arn,
)

# EC2 Instance Role for ECS instances
ecs_instance_role = aws.iam.Role(
    "ecs-instance-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Service": "ec2.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }]
    }""",
    tags={
        "Name": f"{project_name}-{environment}-ecs-instance-role",
        "Environment": environment,
    },
)

# Attach AWS managed policy for ECS instances
ecs_instance_policy_attachment = aws.iam.RolePolicyAttachment(
    "ecs-instance-policy-attachment",
    role=ecs_instance_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
)

# Create instance profile for EC2 instances
ecs_instance_profile = aws.iam.InstanceProfile(
    "ecs-instance-profile",
    role=ecs_instance_role.name,
)

# ============================================================================
# Security Groups
# ============================================================================

# Security group for Application Load Balancer
alb_security_group = aws.ec2.SecurityGroup(
    "alb-security-group",
    vpc_id=vpc.id,
    description="Security group for Application Load Balancer",
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
        ),
    ],
    tags={
        "Name": f"{project_name}-{environment}-alb-sg",
        "Environment": environment,
    },
)

# Security group for ECS tasks
ecs_security_group = aws.ec2.SecurityGroup(
    "ecs-security-group",
    vpc_id=vpc.id,
    description="Security group for ECS tasks",
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=8080,
            to_port=8080,
            security_groups=[alb_security_group.id],
            description="Allow traffic from ALB",
        ),
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all outbound traffic",
        ),
    ],
    tags={
        "Name": f"{project_name}-{environment}-ecs-sg",
        "Environment": environment,
    },
)

# ============================================================================
# Application Load Balancer
# ============================================================================

# Create Application Load Balancer
alb = aws.lb.LoadBalancer(
    "inference-alb",
    load_balancer_type="application",
    subnets=[s.id for s in public_subnets],
    security_groups=[alb_security_group.id],
    enable_deletion_protection=False,
    tags={
        "Name": f"{project_name}-{environment}-alb",
        "Environment": environment,
    },
)

# Create target group for ECS service
target_group = aws.lb.TargetGroup(
    "inference-target-group",
    port=8080,
    protocol="HTTP",
    vpc_id=vpc.id,
    target_type="ip",
    deregistration_delay=30,
    health_check=aws.lb.TargetGroupHealthCheckArgs(
        enabled=True,
        path="/health",
        protocol="HTTP",
        matcher="200",
        interval=30,
        timeout=5,
        healthy_threshold=2,
        unhealthy_threshold=3,
    ),
    tags={
        "Name": f"{project_name}-{environment}-tg",
        "Environment": environment,
    },
)

# Create ALB listener
alb_listener = aws.lb.Listener(
    "inference-listener",
    load_balancer_arn=alb.arn,
    port=80,
    protocol="HTTP",
    default_actions=[
        aws.lb.ListenerDefaultActionArgs(
            type="forward",
            target_group_arn=target_group.arn,
        )
    ],
)

# ============================================================================
# ECS Cluster with GPU Support
# ============================================================================

# Create ECS cluster
ecs_cluster = aws.ecs.Cluster(
    "gpu-inference-cluster",
    settings=[
        aws.ecs.ClusterSettingArgs(
            name="containerInsights",
            value="enabled",
        )
    ],
    tags={
        "Name": f"{project_name}-{environment}-cluster",
        "Environment": environment,
    },
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

# User data script to configure ECS agent and GPU support
user_data = pulumi.Output.all(ecs_cluster.name).apply(
    lambda args: f"""#!/bin/bash
echo ECS_CLUSTER={args[0]} >> /etc/ecs/ecs.config
echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
"""
)

# Launch template for GPU instances
launch_template = aws.ec2.LaunchTemplate(
    "ecs-gpu-launch-template",
    image_id=ecs_gpu_ami.id,
    instance_type="g4dn.xlarge",
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        arn=ecs_instance_profile.arn,
    ),
    vpc_security_group_ids=[ecs_security_group.id],
    user_data=user_data.apply(lambda ud: pulumi.Output.secret(ud).apply(lambda s: s)),
    block_device_mappings=[
        aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
            device_name="/dev/xvda",
            ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
                volume_size=100,
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
            tags={
                "Name": f"{project_name}-{environment}-ecs-gpu-instance",
                "Environment": environment,
                "Cluster": ecs_cluster.name,
            },
        )
    ],
)

# Auto Scaling Group for GPU instances
asg = aws.autoscaling.Group(
    "ecs-gpu-asg",
    desired_capacity=1,
    max_size=3,
    min_size=1,
    vpc_zone_identifiers=[s.id for s in private_subnets],
    launch_template=aws.autoscaling.GroupLaunchTemplateArgs(
        id=launch_template.id,
        version="$Latest",
    ),
    health_check_type="EC2",
    health_check_grace_period=300,
    protect_from_scale_in=True,
    tags=[
        aws.autoscaling.GroupTagArgs(
            key="Name",
            value=f"{project_name}-{environment}-ecs-gpu-asg",
            propagate_at_launch=True,
        ),
        aws.autoscaling.GroupTagArgs(
            key="Environment",
            value=environment,
            propagate_at_launch=True,
        ),
        aws.autoscaling.GroupTagArgs(
            key="AmazonECSManaged",
            value="true",
            propagate_at_launch=True,
        ),
    ],
)

# Capacity provider for the ECS cluster
capacity_provider = aws.ecs.CapacityProvider(
    "gpu-capacity-provider",
    auto_scaling_group_provider=aws.ecs.CapacityProviderAutoScalingGroupProviderArgs(
        auto_scaling_group_arn=asg.arn,
        managed_scaling=aws.ecs.CapacityProviderAutoScalingGroupProviderManagedScalingArgs(
            status="ENABLED",
            target_capacity=100,
            minimum_scaling_step_size=1,
            maximum_scaling_step_size=1,
        ),
        managed_termination_protection="ENABLED",
    ),
)

# Associate capacity provider with cluster
cluster_capacity_providers = aws.ecs.ClusterCapacityProviders(
    "cluster-capacity-providers",
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
    "inference-logs",
    retention_in_days=7,
    tags={
        "Name": f"{project_name}-{environment}-logs",
        "Environment": environment,
    },
)

# ============================================================================
# ECS Task Definition
# ============================================================================

# Create ECS task definition with GPU support
task_definition = aws.ecs.TaskDefinition(
    "inference-task",
    family=f"{project_name}-{environment}-inference",
    network_mode="awsvpc",
    requires_compatibilities=["EC2"],
    cpu="4096",
    memory="16384",
    execution_role_arn=ecs_task_execution_role.arn,
    task_role_arn=ecs_task_role.arn,
    container_definitions=pulumi.Output.all(
        log_group.name, model_bucket.id, current.name
    ).apply(
        lambda args: f"""[
        {{
            "name": "inference-api",
            "image": "public.ecr.aws/docker/library/python:3.11-slim",
            "cpu": 4096,
            "memory": 16384,
            "essential": true,
            "portMappings": [
                {{
                    "containerPort": 8080,
                    "protocol": "tcp"
                }}
            ],
            "environment": [
                {{
                    "name": "MODEL_BUCKET",
                    "value": "{args[1]}"
                }},
                {{
                    "name": "AWS_REGION",
                    "value": "{args[2]}"
                }},
                {{
                    "name": "PORT",
                    "value": "8080"
                }}
            ],
            "command": [
                "python3",
                "-m",
                "http.server",
                "8080"
            ],
            "healthCheck": {{
                "command": ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"],
                "interval": 30,
                "timeout": 5,
                "retries": 3,
                "startPeriod": 60
            }},
            "logConfiguration": {{
                "logDriver": "awslogs",
                "options": {{
                    "awslogs-group": "{args[0]}",
                    "awslogs-region": "{args[2]}",
                    "awslogs-stream-prefix": "inference"
                }}
            }},
            "resourceRequirements": [
                {{
                    "type": "GPU",
                    "value": "1"
                }}
            ]
        }}
    ]"""
    ),
    tags={
        "Name": f"{project_name}-{environment}-task",
        "Environment": environment,
    },
)

# ============================================================================
# ECS Service
# ============================================================================

# Create ECS service with load balancer integration
ecs_service = aws.ecs.Service(
    "inference-service",
    cluster=ecs_cluster.arn,
    task_definition=task_definition.arn,
    desired_count=1,
    launch_type="EC2",
    scheduling_strategy="REPLICA",
    deployment_maximum_percent=200,
    deployment_minimum_healthy_percent=100,
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
        subnets=[s.id for s in private_subnets],
        security_groups=[ecs_security_group.id],
        assign_public_ip=False,
    ),
    load_balancers=[
        aws.ecs.ServiceLoadBalancerArgs(
            target_group_arn=target_group.arn,
            container_name="inference-api",
            container_port=8080,
        )
    ],
    capacity_provider_strategies=[
        aws.ecs.ServiceCapacityProviderStrategyArgs(
            capacity_provider=capacity_provider.name,
            weight=1,
            base=1,
        )
    ],
    health_check_grace_period_seconds=60,
    tags={
        "Name": f"{project_name}-{environment}-service",
        "Environment": environment,
    },
    opts=pulumi.ResourceOptions(depends_on=[alb_listener, cluster_capacity_providers]),
)

# ============================================================================
# CloudWatch Monitoring and Alarms
# ============================================================================

# CPU Utilization Alarm
cpu_alarm = aws.cloudwatch.MetricAlarm(
    "ecs-cpu-alarm",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="CPUUtilization",
    namespace="AWS/ECS",
    period=300,
    statistic="Average",
    threshold=80,
    alarm_description="Alert when ECS CPU exceeds 80%",
    dimensions={
        "ClusterName": ecs_cluster.name,
        "ServiceName": ecs_service.name,
    },
    tags={
        "Name": f"{project_name}-{environment}-cpu-alarm",
        "Environment": environment,
    },
)

# Memory Utilization Alarm
memory_alarm = aws.cloudwatch.MetricAlarm(
    "ecs-memory-alarm",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="MemoryUtilization",
    namespace="AWS/ECS",
    period=300,
    statistic="Average",
    threshold=80,
    alarm_description="Alert when ECS memory exceeds 80%",
    dimensions={
        "ClusterName": ecs_cluster.name,
        "ServiceName": ecs_service.name,
    },
    tags={
        "Name": f"{project_name}-{environment}-memory-alarm",
        "Environment": environment,
    },
)

# ALB Target Health Alarm
target_health_alarm = aws.cloudwatch.MetricAlarm(
    "alb-target-health-alarm",
    comparison_operator="LessThanThreshold",
    evaluation_periods=2,
    metric_name="HealthyHostCount",
    namespace="AWS/ApplicationELB",
    period=60,
    statistic="Average",
    threshold=1,
    alarm_description="Alert when no healthy targets",
    dimensions={
        "TargetGroup": target_group.arn_suffix,
        "LoadBalancer": alb.arn_suffix,
    },
    tags={
        "Name": f"{project_name}-{environment}-target-health-alarm",
        "Environment": environment,
    },
)

# ============================================================================
# Exports
# ============================================================================

pulumi.export("vpc_id", vpc.id)
pulumi.export("public_subnet_ids", [s.id for s in public_subnets])
pulumi.export("private_subnet_ids", [s.id for s in private_subnets])
pulumi.export("model_bucket_name", model_bucket.id)
pulumi.export("model_bucket_arn", model_bucket.arn)
pulumi.export("ecs_cluster_name", ecs_cluster.name)
pulumi.export("ecs_service_name", ecs_service.name)
pulumi.export("alb_dns_name", alb.dns_name)
pulumi.export("alb_url", alb.dns_name.apply(lambda dns: f"http://{dns}"))
pulumi.export("log_group_name", log_group.name)
