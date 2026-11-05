"""Reviewed Terraform JSON configuration packages. Never executes Terraform."""
from __future__ import annotations
import hashlib
import io
import json
import zipfile

VERSIONS = {"AWS": ("aws", "5.100.0"), "Azure": ("azurerm", "4.30.0"), "GCP": ("google", "6.30.0")}

def configuration(candidate: dict, kind: str = "VM") -> dict:
    provider = candidate["provider"]
    short, version = VERSIONS[provider]
    os_name = candidate.get("os", "Linux")
    if os_name not in {"Linux", "Windows"} or kind not in {"VM", "Kubernetes"}:
        raise ValueError("Unsupported POC target")
    if kind == "Kubernetes" and os_name != "Linux":
        raise ValueError("Kubernetes workers must use Linux in this POC")
    variables = {"name": {"type": "string"}, "region": {"type": "string", "default": candidate["region"]}}
    def var(name, default=None, sensitive=False):
        variables[name] = {"type": "string"}
        if default is not None:
            variables[name]["default"] = default
        if sensitive:
            variables[name]["sensitive"] = True
        return "${var." + name + "}"
    def ref(value):
        return "${" + value + "}"
    resources = {}
    conf = {"terraform": {"required_version": ">= 1.10, < 2.0", "backend": {"s3": {}},
                           "required_providers": {short: {"source": "hashicorp/" + short, "version": "= " + version}}},
            "provider": {short: {}}, "variable": variables, "resource": resources}
    if provider == "AWS":
        conf["provider"][short] = {"region": "${var.region}", "assume_role": [{"role_arn": var("execution_role_arn"), "external_id": var("external_id", sensitive=True)}]}
        if kind == "VM":
            resources["aws_instance"] = {"vm": {"ami": var("image_id"), "instance_type": candidate["sku"],
                "subnet_id": var("subnet_id"), "vpc_security_group_ids": [var("security_group_id")],
                "associate_public_ip_address": False, "metadata_options": [{"http_tokens": "required"}],
                "root_block_device": [{"encrypted": True, "volume_size": 64, "volume_type": "gp3"}],
                "tags": {"Name": "${var.name}", "ManagedBy": "Innominds-IIPP-Terraform"}}}
        else:
            resources["aws_eks_cluster"] = {"cluster": {"name": "${var.name}", "role_arn": var("cluster_role_arn"),
                "version": var("kubernetes_version"), "vpc_config": [{"subnet_ids": [var("subnet_id"), var("second_subnet_id")],
                "endpoint_private_access": True, "endpoint_public_access": False}], "encryption_config": [{"resources": ["secrets"], "provider": [{"key_arn": var("kms_key_arn")}]}]}}
            resources["aws_eks_node_group"] = {"workers": {"cluster_name": ref("aws_eks_cluster.cluster.name"),
                "node_group_name": "workers", "node_role_arn": var("node_role_arn"), "subnet_ids": [var("subnet_id"), var("second_subnet_id")],
                "instance_types": [candidate["sku"]], "version": "${var.kubernetes_version}", "ami_type": "AL2023_x86_64_STANDARD", "disk_size": 64,
                "scaling_config": [{"desired_size": candidate["cluster"]["node_count"], "min_size": candidate["cluster"]["node_count"], "max_size": candidate["cluster"]["node_count"]}]}}
    elif provider == "Azure":
        conf["provider"][short] = {"features": [{}], "subscription_id": var("subscription_id")}
        if kind == "VM":
            resources["azurerm_network_interface"] = {"nic": {"name": "${var.name}-nic", "location": "${var.region}",
                "resource_group_name": var("resource_group"), "ip_configuration": [{"name": "private", "subnet_id": var("subnet_id"), "private_ip_address_allocation": "Dynamic"}]}}
            resources["azurerm_network_interface_security_group_association"] = {"security": {"network_interface_id": ref("azurerm_network_interface.nic.id"), "network_security_group_id": var("network_security_group_id")}}
            vm = {"name": "${var.name}", "location": "${var.region}", "resource_group_name": "${var.resource_group}",
                  "size": candidate["sku"], "admin_username": var("admin_username"), "network_interface_ids": [ref("azurerm_network_interface.nic.id")],
                  "source_image_id": var("image_id"), "os_disk": [{"caching": "ReadWrite", "storage_account_type": "StandardSSD_LRS", "disk_size_gb": 128 if os_name == "Windows" else 64}],
                  "encryption_at_host_enabled": True}
            if os_name == "Windows":
                vm["admin_password"] = var("admin_password", sensitive=True)
            else:
                vm["disable_password_authentication"] = True
                vm["admin_ssh_key"] = [{"username": "${var.admin_username}", "public_key": var("ssh_public_key")}]
            resources["azurerm_windows_virtual_machine" if os_name == "Windows" else "azurerm_linux_virtual_machine"] = {"vm": vm}
        else:
            resources["azurerm_kubernetes_cluster"] = {"cluster": {"name": "${var.name}", "location": "${var.region}",
                "resource_group_name": var("resource_group"), "dns_prefix": "${var.name}", "kubernetes_version": var("kubernetes_version"),
                "private_cluster_enabled": True, "role_based_access_control_enabled": True, "identity": [{"type": "SystemAssigned"}],
                "default_node_pool": [{"name": "system", "vm_size": candidate["sku"], "node_count": candidate["cluster"]["node_count"], "vnet_subnet_id": var("subnet_id")}],
                "network_profile": [{"network_plugin": "azure", "network_policy": "azure"}]}}
    else:
        conf["provider"][short] = {"project": var("project_id"), "region": "${var.region}"}
        if kind == "VM":
            resources["google_compute_instance"] = {"vm": {"name": "${var.name}", "zone": var("zone"), "machine_type": candidate["sku"],
                "boot_disk": [{"initialize_params": [{"image": var("image_id"), "size": 64, "type": "pd-balanced"}]}],
                "network_interface": [{"subnetwork": var("subnet_id")}], "shielded_instance_config": [{"enable_vtpm": True, "enable_integrity_monitoring": True}],
                "metadata": {"enable-oslogin": "TRUE", "block-project-ssh-keys": "TRUE"}}}
        else:
            resources["google_container_cluster"] = {"cluster": {"name": "${var.name}", "location": "${var.region}",
                "network": var("network_id"), "subnetwork": var("subnet_id"), "min_master_version": var("kubernetes_version"),
                "remove_default_node_pool": True, "initial_node_count": 1, "deletion_protection": True,
                "private_cluster_config": [{"enable_private_nodes": True, "enable_private_endpoint": True, "master_ipv4_cidr_block": var("master_cidr")}],
                "ip_allocation_policy": [{"cluster_secondary_range_name": var("pod_range_name"), "services_secondary_range_name": var("service_range_name")}],
                "workload_identity_config": [{"workload_pool": "${var.project_id}.svc.id.goog"}]}}
            resources["google_container_node_pool"] = {"workers": {"name": "workers", "location": "${var.region}", "cluster": ref("google_container_cluster.cluster.name"),
                "node_count": candidate["cluster"]["node_count"], "node_locations": [var("zone")], "version": "${var.kubernetes_version}",
                "node_config": [{"machine_type": candidate["sku"], "image_type": "COS_CONTAINERD", "service_account": var("node_service_account"), "oauth_scopes": ["https://www.googleapis.com/auth/cloud-platform"]}]}}
    outputs = {}
    if provider == "AWS":
        if kind == "VM":
            outputs = {"resource_name": "var.name", "instance_id": "aws_instance.vm.id",
                       "private_ip": "aws_instance.vm.private_ip", "public_ip": "aws_instance.vm.public_ip"}
        else:
            outputs = {"resource_name": "aws_eks_cluster.cluster.name", "cluster_arn": "aws_eks_cluster.cluster.arn",
                       "kubernetes_version": "aws_eks_cluster.cluster.version",
                       "node_group_name": "aws_eks_node_group.workers.node_group_name"}
    elif provider == "Azure":
        resource = "azurerm_kubernetes_cluster.cluster" if kind == "Kubernetes" else ("azurerm_windows_virtual_machine.vm" if os_name == "Windows" else "azurerm_linux_virtual_machine.vm")
        outputs = {"resource_name": resource + ".name", "resource_id": resource + ".id"}
        if kind == "VM":
            outputs["private_ip"] = "azurerm_network_interface.nic.private_ip_address"
    else:
        resource = "google_container_cluster.cluster" if kind == "Kubernetes" else "google_compute_instance.vm"
        outputs = {"resource_name": resource + ".name", "resource_id": resource + ".id"}
        if kind == "VM":
            outputs["private_ip"] = resource + ".network_interface[0].network_ip"
    conf["output"] = {name: {"value": ref(expression)} for name, expression in outputs.items()}
    if kind == "Kubernetes":
        conf["output"]["worker_count"] = {"value": candidate["cluster"]["node_count"]}
    return conf

def package(candidate: dict, request: dict, kind="VM") -> bytes:
    config = configuration(candidate, kind)
    content = json.dumps(config, indent=2).encode()
    manifest = {"record_version": 1, "module_version": "poc-v1", "status": "CONFIGURATION_ONLY",
                "message": "Configuration generated — Terraform plan not run", "sha256": hashlib.sha256(content).hexdigest(),
                "candidate": candidate, "requirements": request, "live_execution_validated": False}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.tf.json", content)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("README.txt", "Configuration generated — Terraform plan not run.\nSupply approved inputs and validate images, quotas, versions, network policy and permissions.\nUse the central S3 backend with native locking; never use local state.\nWindows Azure passwords must be supplied through a protected runtime secret; state contains sensitive values.\nGKE deletion protection requires an independently reviewed change before destroy.\nNo licence terms are accepted automatically. No credentials are included.\n")
    return output.getvalue()
