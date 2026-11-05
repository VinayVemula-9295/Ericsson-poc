# Lifecycle release verification — 2026-11-05

Deployed using the deployment skill at
`C:/Users/vvemula/.codex/skills/deployment/SKILL.md`.

- Endpoint: http://13.203.223.14:8501
- Release: `20261105T061226Z`
- Remote source: `/home/ubuntu/intelligent-provisioning-platform-releases/20261105T061226Z/intelligent-provisioning-platform`
- Local archive: `intelligent-provisioning-platform-20261105T061226Z.zip` at the workspace root
- Archive size: 466627 bytes
- SHA-256: `694991f596541d2e2a9c3b21059ce2acf23de0bec70755e82c899b5cd2be52ce`
- Backup: `20261105-061350`; previous source and container retained
- Health: running, endpoint `ok`, deployed source hash verified, no uncaught application exceptions in inspected logs
- Configuration and persistent/secret mounts preserved; both provisioning flags remain false
- Full Linux suite: 181 passed in 20.86 seconds in an isolated container
- Live browser: Work lifecycle removed; Deployments history and details dialog render without application errors
- Destroy dialog and backend approvals tested with mocks; no live apply/destroy performed

Rollback on the host:

```bash
cd /home/ubuntu/intelligent-provisioning-platform-releases/20261105T061226Z/intelligent-provisioning-platform
./rollback.sh 20261105-061350
```

After rollback, reconcile active-release metadata with the restored container.

## Outstanding live work

The local dev administrator profile returns ExpiredToken. The controller role
cannot assume OrganizationAccountAccessRole in Demo or Test. Target IAM repairs,
KMS/network verification, planning enablement and the four live EC2/EKS plans
remain incomplete. Refresh an authorized administrator session to finish these
steps; no live target IAM or infrastructure mutations were attempted.
