# Dashboard and saved Terraform reuse

The dashboard presents three provider cards. Each View action opens the selected
provider's account details. Counts come from configured connections; Connected
reflects the last verification. Account-scoped month-to-date billing remains
Unavailable. Recent provisioning shows the five newest saved records, and View all
opens Deployment history with reset filters. History supports text, status and
provider filters, ten-record pages, and IST dates. Successful AWS rows expose
Reprovision and reviewed Destroy; other rows expose Details.

Provisioning Agent has Create new infrastructure and Reuse previous deployment
modes. Reuse supports text search and advanced provider/account/region/date
filters. Selecting an eligible saved record opens Terraform inputs directly,
without sending a prompt or rerunning Agent recommendations. Supply a new name;
account and region can change. Target changes discard saved target-specific input
defaults and refresh the existing live image/network/IAM choices. Original instance
size, OS and resource type remain fixed. Current availability and policy are
revalidated before planning.

Both Terraform stores now record a version 1 `reuse_manifest` containing SHA-256
checksums of Terraform source, non-secret input values and the dependency lock file.
Files remain in their original runtime directories after apply or destroy. A new
deployment copies source and the dependency lock file, writes its own inputs, and
allocates a new S3 state key. It never copies state, binary plans, approvals,
credentials or working caches. Records link back through `source_deployment` and
`source_artifact_sha256`. Missing, added or modified source files block reuse.

Earlier Agent records can be read using their existing configuration/input/lock
checksums. Earlier legacy records without a complete verified source bundle remain
visible but require a new infrastructure request. Only records whose current status
is SUCCEEDED are eligible when their bundle verifies. Failed, destroyed, active and
approval-pending records cannot be reused, including through backend entry points.
Historical state is never migrated by reuse.

Details shows non-secret saved inputs for each successful deployment. Submit from
the prefilled reuse form prepares a fresh saved plan and requires the existing human
approval workflow. Editing inputs invalidates the displayed plan. Execution flags,
provider restrictions, backend identity checks, locking and audit remain in force.
Azure/GCP script execution remains unavailable. No release deployment or live
Terraform mutation is part of this UI change.

Portable regression coverage is in `tests/test_saved_script_reuse.py`; existing
history and destroy UI checks cover reviewed lifecycle operations. Full Terraform
safety verification requires Linux (`fcntl`). Browser smoke checks use isolated
local data with execution disabled and do not establish live cloud success.
