# Governed control-plane workflow

The existing Streamlit application now has an opt-in customer-scoped workflow.
Enable `CONTROL_PLANE_ENABLED=true` to replace demo access with authenticated local
users and route new infrastructure execution through an approval queue and worker.
Existing demo behavior remains available with this flag disabled; it is not a
secured multi-user deployment. Do not expose demo mode as a production service.

## Configure

Create a protected identity directory outside the checkout, then run:

```sh
python tools/control_plane_user.py --file /run/iipp/users.json --username admin --role administrator
python tools/control_plane_user.py --file /run/iipp/users.json --username lead --role requester --customer Acme
python tools/control_plane_user.py --file /run/iipp/users.json --username reviewer --role approver --customer Acme
```

Passwords are entered interactively and stored as salted PBKDF2-SHA256 hashes
(600,000 iterations). Protect the registry with host filesystem permissions and
mount it read-only into both processes. Do not include it in release archives.
Setting an identity's `disabled` field to true revokes access on the next action;
the worker rechecks both requester and reviewer permissions before execution.
Serve the app behind an HTTPS gateway with login rate limiting. Enterprise SSO
integration is not included in this initial local authentication implementation.

Set both processes to the same `CONTROL_PLANE_USERS_FILE=/run/iipp/users.json`,
`CONTROL_PLANE_ENABLED=true`, persistent local `DATA_DIR`, Terraform state settings,
and existing cloud credentials. Keep the existing execution switches disabled
until the runner and permissions have been validated. Start the worker separately:

```sh
python control_plane_worker.py
# For a single polling pass:
python control_plane_worker.py --once
```

The Docker image now starts `runtime_entrypoint.py`, which supervises Streamlit
and the worker together when enabled. If either exits, the container restarts both;
container shutdown and rollback also stop the worker. Do not start an additional
worker for this container. Direct non-Docker launches still need a service supervisor.
Both processes must share the same local
SQLite database volume; this design does not support distributed hosts or SQLite
on network filesystems. For `deploy.sh`, export `CONTROL_PLANE_ENABLED=true` and
`CONTROL_PLANE_USERS_HOST_FILE` pointing to the protected host registry (default
`$HOME/.cloudpilot-control-plane-users.json`). The registry is mounted read-only at
`/run/secrets/control-plane-users.json`. The script validates administrator access
before starting deployment. Setup does not submit or approve provisioning requests.

## User flow

1. An administrator configures a cloud connection and assigns its customer,
   project and organization under **Approvals & schedules**. Ownership changes
   are blocked while requests are pending, approved or running.
2. Requesters see their permitted connections, deployment history and dashboard.
   The dashboard supports customer filtering and explicit live inventory refresh.
   Failed inventory is unavailable; sample infrastructure is not substituted.
3. Use a Linux/Windows/Kubernetes request template, the new-request flow or
   deployment reuse to evaluate options,
   review estimates and generate IaC and a saved Terraform plan. The backend
   records the authenticated initiator rather than accepting a supplied identity.
   Previous deployment Details also offers editing requirements and evaluating new
   options. This produces a fresh comparison and plan rather than modifying a
   previously approved artifact or silently changing the saved reuse bundle.
4. The saved-plan Apply dialog submits an immediate or scheduled request. Times
   are explicitly UTC. Submission never applies infrastructure.
5. A different user with approver or administrator access reviews the saved plan,
   target and execution time and approves or rejects it from the inbox.
6. The worker transactionally claims an approved due request once, revalidates
   identity permissions and artifact fingerprints, then invokes the existing
   provider execution path. Runtime flags, approval token, plan checksums, policy,
   target validation, locks and S3 backend checks remain enforced.

Agent plans expire after one hour and legacy AWS plans after four hours. Scheduling
beyond this window is refused. Expired or changed plans become NEEDS_REVIEW;
generate and approve a new plan, never silently regenerate an approved artifact.
The approval token is loaded only by the runner and is never stored in the queue.

Database events retain submit, approve/reject, claim and completion decisions.
Duplicate submissions for the same plan are refused. A worker crash may leave a
request RUNNING: an operator must reconcile actual cloud resources and Terraform
state before any new request. Automatic replay of uncertain executions is disabled.
Back up the SQLite database alongside existing plans, state and connection metadata.

## Coverage and remaining work

Implemented here: local authenticated roles, customer connection ownership,
customer-authorized connection/history visibility, live inventory refresh, request
submission, independent approvals, bounded scheduling, durable worker claims,
audit events, and existing deployment reuse through the same approval path.

Existing hardware ranking, pricing evidence and Terraform provider implementations
are preserved. This change does not establish live Azure/GCP plan/apply success.
Consolidated billing ingestion, fleet utilization/idle detection, automatic cloud
organization hierarchy discovery, on-premises connectors, arbitrary sizing edits
to reused templates, enterprise SSO and application dependency/scaling intelligence
remain separate work. Dashboard billing and idle totals are explicitly unavailable.
Destroy retains its existing reviewed token workflow, restricted to authorized
approver/admin users; destruction is not scheduled by this queue.
