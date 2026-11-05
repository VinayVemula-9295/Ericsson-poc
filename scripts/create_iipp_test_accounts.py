#!/usr/bin/env python3
"""Create two dedicated AWS Organizations member accounts (dry-run by default).

Requires an AWS Organizations management-account identity with CreateAccount,
TagResource, CreateOrganizationalUnit, MoveAccount and List/Describe permissions.
Use unique, reachable account email addresses; account creation is asynchronous.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from typing import Any
import boto3


def paged(client, operation: str, result_key: str, **kwargs):
    paginator = client.get_paginator(operation)
    for page in paginator.paginate(**kwargs):
        yield from page.get(result_key, [])


def find_ou(org, root_id: str, name: str) -> str | None:
    for item in paged(org, "list_organizational_units_for_parent", "OrganizationalUnits", ParentId=root_id):
        if item.get("Name") == name:
            return item["Id"]
    return None


def find_account(org, email: str, expected_name: str) -> dict[str, Any] | None:
    for account in paged(org, "list_accounts", "Accounts"):
        if account.get("Email", "").lower() == email.lower():
            if account.get("Name") != expected_name:
                raise RuntimeError(f"Email {email} already belongs to account '{account.get('Name')}'. Refusing to reuse it.")
            return account
    return None


def wait_account(org, request_id: str, timeout_seconds: int = 1800) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = org.describe_create_account_status(CreateAccountRequestId=request_id)["CreateAccountStatus"]
        state = result.get("State")
        print(f"Account creation status: {state}")
        if state == "SUCCEEDED":
            return {"Id": result.get("AccountId"), "Name": result.get("AccountName"), "Email": result.get("AccountEmail")}
        if state == "FAILED":
            raise RuntimeError(f"AWS account creation failed: {result.get('FailureReason')} — {result.get('FailureMessage')}")
        time.sleep(15)
    raise TimeoutError("Timed out waiting for account creation. Check Organizations > AWS accounts; do not blindly retry CreateAccount.")


def ensure_account(org, name: str, email: str, ou_id: str, execute: bool) -> dict[str, Any]:
    account = find_account(org, email, name)
    if account:
        account_id = account["Id"]
        print(f"Reusing exact existing account: {name} ({account_id})")
    elif not execute:
        print(f"DRY RUN: would create member account {name} using {email}")
        return {"name": name, "email": email, "account_id": None, "status": "PLANNED"}
    else:
        response = org.create_account(
            AccountName=name, Email=email, RoleName="OrganizationAccountAccessRole",
            IamUserAccessToBilling="DENY",
            Tags=[{"Key": "Application", "Value": "Innominds-IIPP"},
                  {"Key": "Environment", "Value": "non-production"},
                  {"Key": "ManagedBy", "Value": "AWS-Organizations"}],
        )
        status = wait_account(org, response["CreateAccountStatus"]["Id"])
        account_id = status["Id"]
    parents = org.list_parents(ChildId=account_id).get("Parents", [])
    current_parent = parents[0]["Id"] if parents else None
    if current_parent != ou_id:
        if execute:
            org.move_account(AccountId=account_id, SourceParentId=current_parent, DestinationParentId=ou_id)
        print(f"{'Moved' if execute else 'DRY RUN: would move'} {name} into the IIPP non-production OU")
    return {"name": name, "email": email, "account_id": account_id,
            "status": "ACTIVE" if execute else "EXISTING/PLANNED"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poc-email", default=os.getenv("IIPP_POC_ACCOUNT_EMAIL"))
    parser.add_argument("--test-email", default=os.getenv("IIPP_TEST_ACCOUNT_EMAIL"))
    parser.add_argument("--ou-name", default="Innominds-IIPP-NonProduction")
    parser.add_argument("--execute", action="store_true", help="Actually create/move accounts. Default is dry-run.")
    args = parser.parse_args()
    if not args.poc_email or not args.test_email:
        parser.error("Provide --poc-email and --test-email (or IIPP_POC_ACCOUNT_EMAIL / IIPP_TEST_ACCOUNT_EMAIL).")
    if args.poc_email.lower() == args.test_email.lower():
        parser.error("POC and TEST account email addresses must be different.")
    if not args.execute:
        print("DRY RUN MODE — no organization changes will be made.")
    org = boto3.client("organizations")
    roots = org.list_roots().get("Roots", [])
    if not roots:
        raise RuntimeError("No AWS Organizations root found. Run this from the organization's management account.")
    root_id = roots[0]["Id"]
    ou_id = find_ou(org, root_id, args.ou_name)
    if not ou_id:
        if args.execute:
            ou_id = org.create_organizational_unit(ParentId=root_id, Name=args.ou_name)["OrganizationalUnit"]["Id"]
            print(f"Created OU {args.ou_name}: {ou_id}")
        else:
            print(f"DRY RUN: would create OU '{args.ou_name}' under {root_id}")
            ou_id = "ou-dry-run"
    results = []
    for name, email in (("Innominds-IIPP-POC", args.poc_email), ("Innominds-IIPP-TEST", args.test_email)):
        results.append(ensure_account(org, name, email, ou_id, args.execute))
    print(json.dumps({"organizational_unit": args.ou_name, "accounts": results}, indent=2))
    if not args.execute:
        print("Review the unique email addresses and rerun with --execute from the AWS Organizations management account.")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
