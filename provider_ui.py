"""Streamlit provider configuration panel. Secrets are sent straight to Secrets Manager."""
from __future__ import annotations

import json
import re
from typing import Any

import streamlit as st

from provider_connections import (
    list_connections, save_provider_connection, test_provider_credentials,
)

_SECRET_WIDGET_KEYS = [
    "iipp_aws_access_key", "iipp_aws_secret_key", "iipp_aws_external_id",
    "iipp_azure_client_secret", "iipp_gcp_json_key", "iipp_gcp_key_file",
]


def _clear_sensitive_widget_state() -> None:
    if st.session_state.pop("iipp_clear_provider_secrets", False):
        for key in _SECRET_WIDGET_KEYS:
            st.session_state.pop(key, None)


def render_provider_configuration() -> None:
    _clear_sensitive_widget_state()
    close_col, _ = st.columns([1, 5])
    with close_col:
        if st.button("Close", key="iipp-config-close", use_container_width=True):
            st.session_state["iipp_open_provider_config_dialog"] = False
            st.rerun()
    st.subheader("Connect a cloud account")
    st.caption("Credentials are encrypted in AWS Secrets Manager. Only non-secret account metadata is stored by Intelligent Provisioning Platform.")
    if "iipp_provider_select" not in st.session_state:
        st.session_state["iipp_provider_select"] = st.session_state.get("iipp_active_provider", "AWS")
    provider = st.selectbox("Cloud provider", ["AWS", "Azure", "GCP"], key="iipp_provider_select")
    with st.form(f"iipp-config-form-{provider.lower()}", clear_on_submit=False):
        name_default = {"AWS": "AWS-POC", "Azure": "Azure-POC", "GCP": "GCP-POC"}[provider]
        connection_name = st.text_input("Connection name", value=name_default,
                                         help="Use distinct names for separate accounts or subscriptions, for example AWS-POC and AWS-TEST.")
        metadata: dict[str, Any] = {}
        secret_payload: dict[str, Any] = {}

        if provider == "AWS":
            auth_mode = st.radio("Authentication method",
                                 ["EC2 IAM role / AssumeRole (recommended)", "Access keys (advanced)"],
                                 horizontal=False, key="iipp_aws_auth_mode")
            metadata["credential_mode"] = "access_keys" if auth_mode.startswith("Access") else "instance_role"
            if metadata["credential_mode"] == "access_keys":
                secret_payload["access_key_id"] = st.text_input("AWS Access Key ID", key="iipp_aws_access_key")
                secret_payload["secret_access_key"] = st.text_input("AWS Secret Access Key", type="password", key="iipp_aws_secret_key")
            st.markdown("**Target-account role settings (required for POC/TEST provisioning)**")
            metadata["inventory_role_arn"] = st.text_input(
                "Inventory Read Role ARN", placeholder="arn:aws:iam::123456789012:role/IIPPInventoryReadRole",
                help="Used for live resource discovery and telemetry in the selected AWS account.")
            metadata["terraform_role_arn"] = st.text_input(
                "Terraform Execution Role ARN (optional until provisioning is enabled)", placeholder="arn:aws:iam::333347372766:role/IIPPTerraformExecutionRole",
                help="Create this restricted role before enabling Terraform. Leave blank for read-only inventory testing; do not enter an example ARN.")
            secret_payload["external_id"] = st.text_input("External ID", type="password", key="iipp_aws_external_id", help="Must exactly match the sts:ExternalId condition in the target IAM role trust policy.")
            st.caption("Use the same account-specific External ID in the inventory and Terraform role trust policies. It may contain letters, numbers and _+=,.@:/- only.")
            metadata["region"] = st.text_input("Default region name", value="ap-south-1", placeholder="ap-south-1")
            metadata["output_format"] = st.text_input("Default output format", value="json")
        elif provider == "Azure":
            metadata["tenant_id"] = st.text_input("Tenant ID", placeholder="Azure AD / Entra tenant ID")
            metadata["client_id"] = st.text_input("Client ID", placeholder="Application / service principal ID")
            secret_payload["client_secret"] = st.text_input("Client Secret", type="password", key="iipp_azure_client_secret")
            metadata["subscription_id"] = st.text_input("Subscription ID", placeholder="Azure subscription ID")
            metadata["region"] = st.text_input("Default region", value="centralindia", placeholder="centralindia")
        else:
            metadata["service_account_email"] = st.text_input("Service Account Email", placeholder="name@project-id.iam.gserviceaccount.com")
            metadata["project_id"] = st.text_input("Project ID", placeholder="GCP project identifier")
            metadata["region"] = st.text_input("Default region", value="asia-south1", placeholder="asia-south1")
            metadata["zone"] = st.text_input("Default zone", value="asia-south1-a", placeholder="asia-south1-a")
            st.caption("Upload the downloaded service-account JSON key. It contains the private key and required credential metadata. A standalone PEM private key is not sufficient to construct a Google service-account credential safely.")
            key_file = st.file_uploader("JSON Key File", type=["json"], key="iipp_gcp_key_file")
            pasted_json = st.text_area("Or paste complete service-account JSON", height=100,
                                       placeholder='{"type":"service_account", ...}', key="iipp_gcp_json_key")
            if key_file is not None:
                try:
                    secret_payload["service_account_json"] = json.loads(key_file.getvalue().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    st.error("The uploaded GCP key file is not valid UTF-8 JSON.")
            elif pasted_json.strip():
                try:
                    secret_payload["service_account_json"] = json.loads(pasted_json)
                except json.JSONDecodeError:
                    st.error("The pasted service-account JSON is invalid.")

        test_submit = st.form_submit_button("Test connection & save", type="primary")
        save_submit = st.form_submit_button("Save without testing")

    if test_submit or save_submit:
        try:
            if provider == "AWS":
                external_id = str(secret_payload.get("external_id", "")).strip()
                if not external_id:
                    raise ValueError("External ID is required for this AWS cross-account connection.")
                if not re.fullmatch(r"[\w+=,.@:/-]{2,1224}", external_id):
                    raise ValueError("External ID contains unsupported characters. Use only letters, numbers and _+=,.@:/- (no spaces or brackets).")
                inventory_arn = str(metadata.get("inventory_role_arn", "")).strip()
                if not re.fullmatch(r"arn:aws:iam::\d{12}:role/[A-Za-z0-9+=,.@_/-]{1,512}", inventory_arn):
                    raise ValueError("Enter a valid target-account Inventory Read Role ARN.")
                execution_arn = str(metadata.get("terraform_role_arn", "")).strip()
                if execution_arn and not re.fullmatch(r"arn:aws:iam::\d{12}:role/[A-Za-z0-9+=,.@_/-]{1,512}", execution_arn):
                    raise ValueError("Terraform Execution Role ARN is invalid. Leave it blank until the role is created.")
            if test_submit:
                with st.spinner(f"Validating {provider} credentials and querying live inventory…"):
                    test_result = test_provider_credentials(provider, metadata, secret_payload)
                if not test_result.get("ok"):
                    st.error(f"Connection test failed: {test_result.get('error', 'Unknown connection error')}")
                    st.session_state["iipp_clear_provider_secrets"] = True
                    return
            else:
                test_result = {"ok": False}
            saved = save_provider_connection(provider, connection_name, metadata,
                                              secret_payload, test_result)
            st.session_state["iipp_last_provider_save"] = saved
            st.session_state["iipp_clear_provider_secrets"] = True
            if test_result.get("ok"):
                account_ref = saved.get("account_id") or saved.get("subscription_id") or saved.get("project_id") or "verified"
                st.session_state["iipp_connection_success_message"] = f"{provider} connection '{connection_name}' verified and saved · {account_ref}."
                st.session_state["iipp_open_connection_success_dialog"] = True
                st.session_state["iipp_provider_after_save"] = provider
                st.session_state["iipp_connection_after_save"] = saved.get("connection_id")
                st.session_state["iipp_open_provider_config_dialog"] = False
                st.rerun()
            else:
                st.warning(f"{provider} connection '{connection_name}' saved but not tested. Run Test connection before using live data or Terraform.")
        except Exception as exc:
            st.error(f"Could not save {provider} connection: {type(exc).__name__}: {exc}")
            st.session_state["iipp_clear_provider_secrets"] = True

    last_save = st.session_state.get("iipp_last_provider_save")
    if last_save:
        st.caption(f"Last saved: {last_save.get('name')} · {last_save.get('connection_status')} · secret stored: {last_save.get('secret_configured')}")

    st.divider()
    st.markdown("**Saved connections**")
    rows = list_connections()
    if not rows:
        st.info("No provider connections have been saved yet.")
    else:
        for row in rows:
            account = row.get("account_id") or row.get("subscription_id") or row.get("project_id") or "identity not tested"
            st.write(f"**{row.get('provider')} · {row.get('name')}** — {row.get('connection_status', 'UNKNOWN')} · `{account}`")
            if row.get("inventory_source"):
                st.caption(row["inventory_source"])
