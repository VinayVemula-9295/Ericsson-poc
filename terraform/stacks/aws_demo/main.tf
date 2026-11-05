locals {
  required_tags = {
    Project     = "Innominds-IIPP"
    Environment = var.environment
    Owner       = var.owner
    ManagedBy   = "Innominds-IIPP-Terraform"
    TTL_Days    = tostring(var.ttl_days)
  }
}

resource "aws_instance" "iipp_demo" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  associate_public_ip_address = false
  monitoring                  = true
  iam_instance_profile        = var.instance_profile_name == "" ? null : var.instance_profile_name

  root_block_device {
    encrypted             = true
    volume_size           = 64
    volume_type           = "gp3"
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tags = merge(local.required_tags, { Name = var.instance_name })
}
