variable "region" {
  type        = string
  description = "AWS region for this demo deployment"
}

variable "ami_id" {
  type        = string
  description = "Reviewed Linux AMI ID for the selected region"
}

variable "instance_type" {
  type        = string
  description = "Allowed EC2 instance type"
}

variable "subnet_id" {
  type        = string
  description = "Existing approved private subnet ID"
}

variable "security_group_id" {
  type        = string
  description = "Existing reviewed security group ID"
}

variable "instance_name" {
  type        = string
  description = "Name tag for the new demo instance"
}

variable "owner" {
  type        = string
  default     = "Innominds-IIPP"
  description = "Owner tag"
}

variable "ttl_days" {
  type        = number
  default     = 2
  description = "Demo resource TTL metadata; cleanup must be implemented/verified separately"
}

variable "instance_profile_name" {
  type        = string
  default     = ""
  description = "Optional pre-created instance profile name, if approved"
}

variable "target_role_arn" {
  type        = string
  default     = ""
  description = "Optional cross-account Terraform execution role ARN."
}

variable "target_role_external_id" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Optional external ID for target role assumption; supplied at runtime, not written to deployment metadata."
}

variable "environment" {
  type        = string
  default     = "poc"
  description = "Target account environment label: poc or test"
  validation {
    condition     = contains(["poc", "test"], lower(var.environment))
    error_message = "environment must be poc or test."
  }
}
variable "os" {
  type    = string
  default = "Linux"
  validation {
    condition     = contains(["Linux", "Windows"], var.os)
    error_message = "Only Linux and Windows Server are supported."
  }
}
