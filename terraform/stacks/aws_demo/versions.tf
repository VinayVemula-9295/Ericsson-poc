terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

provider "aws" {
  region = var.region

  # The backend keeps using the controller identity for S3 state. This provider
  # assumes the selected target-account execution role when one is configured.
  dynamic "assume_role" {
    for_each = var.target_role_arn == "" ? [] : [var.target_role_arn]
    content {
      role_arn    = assume_role.value
      external_id = var.target_role_external_id == "" ? null : var.target_role_external_id
    }
  }
}
