# Intelligent Provisioning Platform — application flow and prompt checks

Application: http://13.203.223.14:8501

Release scope: Linux and Windows Server VM recommendations, Linux Kubernetes
sizing, official-source evidence, policy checks and Terraform configuration export.
This checklist separates deployed UI behavior from live cloud provisioning.

## 1. Before testing

- Open Work and Agent. Confirm the global cloud/account selector and Configure action.
- Use existing AWS Demo/Test accounts; do not create replacement accounts.
- In Configure, test the intended connection. Credentials belong in the connection
  form/Secrets Manager, never in prompts, screenshots, exported files or this report.
- In Agent, leave **Extended Linux / Windows POC** enabled for the new flow.
- Start each case with Extract requirements, inspect the confirmation form, edit
  its fields, then choose Confirm and find options. Extraction alone must not query clouds.
- Public catalogue options are estimates. A failed/unconfigured provider should
  show missing evidence; an exact SKU or price is not guaranteed by these prompts.
- The deployment preserves Terraform flags disabled and an unconfigured state
  bucket. Configuration downloads should work; saved-plan generation should stop
  until an administrator supplies the existing workflow prerequisites.

## 2. Main application flow

Work inventory → Agent request → Extract requirements → Confirm requirements →
Collect cloud evidence → Apply hard policies → Rank eligible options → Select
configuration → Download Terraform configuration → Optional saved-plan generation
with verified connection → POC review. New-target execution remains disabled.

The top bar selects the Work/legacy context. The extended POC has explicit
AWS/Azure/GCP connection selectors inside its confirmation form; select them
there as well. Unconnected Azure/GCP comparisons use labelled reference hardware
profiles rather than live account specifications.

## 3. Prompt acceptance checklist

| ID | Prompt or action | Confirmation settings / expected behavior |
| --- | --- | --- |
| UI-01 | Open Agent, submit an empty request | Show “Describe your infrastructure requirements first”; no catalogue results or deployment action. |
| VM-01 | `Recommend a Linux VM with 2 vCPU and 8 GiB RAM. Prefer lowest cost.` | Confirm Linux, VM, 2/8. Set Ranking to Lowest Cost explicitly. Select tested connections. Eligible options meet both minimums; exact fits are preferred. |
| VM-02 | `Recommend a Windows Server VM with 4 vCPU and 16 GiB RAM under 250 USD.` | Confirm Windows, VM, 4/16 and budget 250. Windows-specific prices must be used. Missing licence prices mean unavailable evidence, never Linux substitution. |
| VM-03 | `Find a Linux server with 4 vCPU and 9 GiB RAM.` | Confirm 4/9. If no exact option exists, only recommend CPU >=4 and RAM >=9. Distinguish Recommended and Lowest cost. |
| REQ-01 | `I need a server for a customer portal.` | CPU/RAM defaults must be disclosed and editable. Do not treat the prompt as sufficient production sizing evidence. |
| POL-01 | `Recommend a confidential Linux VM with 4 vCPU and 16 GiB RAM. Data must stay only in India.` | Residency confirmation must block comparison until explicit region IDs are supplied. Enter `ap-south-1,centralindia,asia-south1`. Country names are not automatically converted into legal residency guarantees. |
| POL-02 | `Recommend a Linux VM with 2 vCPU and 8 GiB RAM.` | Set permitted regions to `ap-south-1`. Azure/GCP options must be excluded. Decision evidence must show the reason. |
| POL-03 | `Recommend a Linux VM with 2 vCPU and 8 GiB RAM.` | Enable Require verified encryption and Require verified private networking. Without deployment evidence, show unknown controls/no verified eligible option. A private/encrypted template alone is not evidence of an existing deployment. |
| CAP-01 | `Recommend a Linux VM with 4 vCPU and 16 GiB RAM for a traffic spike.` | Enable Require verified quota headroom. If headroom is unknown, exclude the candidate. AWS quota ceilings/offerings must not be described as spare capacity. |
| LAT-01 | `Recommend a Linux VM with 2 vCPU and 8 GiB RAM and latency below 20ms.` | Confirm hard target 20, origin Mumbai, p95, https, 5m. With no uploaded comparable evidence, no compliant latency recommendation. |
| LAT-02 | Repeat LAT-01 using an actual recent probe upload | Match provider/region/origin/metric/method/window. Fresh measured value <=20 passes; >20 fails. Evidence older than 15 minutes, future timestamps or a different origin cannot satisfy the target. |
| K8S-01 | `Recommend Kubernetes with Linux worker nodes of 4 vCPU and 16 GiB RAM.` | Confirm Kubernetes/Linux, replicas 7, pod CPU 1, pod RAM 2 GiB, tolerate one node failure. For a 4/16 node, 20% overhead allows 3 such pods/node; 4 nodes including the spare. Other selected node sizes produce different results. |
| K8S-02 | `Recommend Windows Kubernetes with 4 vCPU and 16 GiB RAM.` | Windows worker nodes must be rejected as outside the POC. Switch OS to Linux to continue. |
| AI-01 | `Recommend a confidential Linux VM with 2 vCPU and 8 GiB RAM.` | Choose Explain verified evidence. Show deterministic output and the sensitive-data restriction; do not send the request to external AI. |
| AI-02 | `Recommend a Linux VM with 2 vCPU and 8 GiB RAM.` | For internal/public data, explanation may use configured AI. It must agree with displayed rankings and prices and identify unavailable evidence. Provider API/model errors are not a successful explanation. |
| SRC-01 | After an ordinary comparison, choose Research official documentation | Show official source URLs, retrieval timestamps and retrieved/unavailable status. Reference text must not be presented as measured latency, quota or launch capacity. |
| ERR-01 | Compare with an unconfigured provider/API or invalid provider region | Show unavailable/failed evidence rather than fixture prices, fabricated inventory or a successful provisioning claim. |

## 4. Latency upload format

Use actual probe results and timestamps. The fixed example below is deliberately
historical and should fail freshness checks after 15 minutes; do not change its
timestamp and present the same number as a new real measurement.

```json
[
  {
    "origin": "Mumbai",
    "provider": "AWS",
    "region": "ap-south-1",
    "metric": "p95",
    "method": "https",
    "window": "5m",
    "measured_at": "2026-11-04T12:00:00Z",
    "value_ms": 12.5
  }
]
```

For a controlled UI-only test, synthetic records may be used only when labelled
synthetic in the test report. They do not validate cloud latency or a customer SLO.
Use Azure/centralindia or GCP/asia-south1 for corresponding provider measurements.

## 5. Terraform configuration download

1. Run VM-01 or VM-02 with at least one priced eligible candidate.
2. Choose Select configuration, then Download Terraform configuration.
3. Open the ZIP locally. It must contain `main.tf.json`, `manifest.json` and `README.txt`.
4. Manifest status must be `CONFIGURATION_ONLY`, live execution validation false,
   with a SHA-256 hash and the confirmed request/candidate snapshot.
5. Inspect provider-pinned resources for the selected provider and OS. Windows
   must not become a Linux VM. No plaintext credentials, passwords or approval tokens.
6. Repeat K8S-01, selecting each available provider: exported resources should be
   EKS, AKS or GKE respectively. Worker OS is Linux. Unknown service versions and
   control-plane/storage/network costs must remain explicit.
7. A configuration ZIP is not a binary Terraform plan and must not claim apply success.

## 6. Optional saved-plan and review checks

These require an administrator-configured S3 backend, enabled planning flag,
tested provider connection, appropriate IAM/API permissions and protected secrets.
They were not executed as deployment health checks. Do not enable flags simply
to make this checklist pass.

- Expand Generate a saved Terraform plan with a verified connection and inspect
  the required variable schema. Supply only non-secret JSON values.
- AWS inputs include name, provider region, execution role, image, subnet and
  security group. Choose Discover approved AWS base images for image evidence.
  Existing AWS region/family policies still apply; a recommendation is not permission to plan.
- Azure uses subscription, resource group, existing network/security group and a
  provider platform image. Windows additionally requires a protected runner password;
  do not enter it in JSON. Linux requires an administrator name and SSH public key.
- GCP uses project, zone, subnet and a versioned public image. Do not upload private keys in JSON.
- Kubernetes variables include service version, approved networks and node identities;
  use the displayed schema. Service/node compatibility still needs validation.
- With planning disabled or missing prerequisites, expect an actionable failure,
  no plan-ready status and no infrastructure changes.
- With valid prerequisites, expect a readable saved-plan summary and checksum.
  Preparing a plan must not create infrastructure.
- Review requires a POC reviewer name and the existing approval token. An invalid
  token, expired plan (>1 hour), changed checksum, identity or policy must reject review.
- Successful POC review records `REVIEWED_EXECUTION_DISABLED`; it does not apply
  a new target. Rejection records `REJECTED`.
- New request/configuration selection must clear the previous saved-plan display.

Legacy AWS apply/destroy remains available with the extended toggle off, subject
to its existing flags, policies, state and explicit approval. That is a separate
cloud-mutation test, not part of deployment verification.

## 7. Record evidence and results

For each case record: test ID, actual UTC time, account/provider, confirmed inputs,
actual result, screenshot or exported filename and status (Pass / Fail / Blocked /
Not run). Record missing credentials/permissions as Blocked, never Pass.

Capture screenshots of requirements, price/specification sources, policy checks,
selected configuration and export manifest. Redact account identifiers as needed;
never capture credentials or approval tokens. Keep sensitive saved plans protected.

Stop acceptance for an unhandled exception, stale result shown as current, wrong
OS pricing, disallowed region recommended, missing credentials replaced by fixtures,
or any infrastructure action executed merely by extracting, comparing or downloading.

## 8. Known release limits

Azure/GCP live execution, complete quota/headroom coverage, guaranteed launch
capacity, Kubernetes version compatibility and complete cluster bills are not
validated. Kubernetes sizing is illustrative. Private cloud, Crossplane, GitOps,
GPU, BYOL, Windows Kubernetes and 200M-subscriber production validation are excluded.
Successful HTTP health and UI checks do not establish those capabilities.
