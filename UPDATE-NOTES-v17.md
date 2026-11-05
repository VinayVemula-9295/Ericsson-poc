# InfraMind v17 — cost-aware multi-cloud recommendations and Agent response

## Recommendation behavior

- Exact hardware match is the first preference when a priceable, eligible exact CPU/RAM profile exists.
- If no exact profile exists, recommend only candidates that meet or exceed both requested minimum vCPU and RAM. Below-minimum profiles remain visible as excluded/closest information and cannot be selected for provisioning.
- Within the preferred tier, the default Balanced strategy weights compute price (65%), workload fit (20%) and availability evidence (15%). Measured end-to-end latency is incorporated only when comparable measurements exist. A Lowest Latency request without measurements returns no latency winner and still exposes the cost-optimal eligible option.
- Optimal is separately labelled as the lowest-priced eligible candidate within the preferred hardware tier. Recommended is the strategy-ranked choice.
- Supplied 4 vCPU / 9 GiB screenshot regression: when AWS `m5a.xlarge` (4 vCPU / 16 GiB, USD 81.03/month) and `c5n.xlarge` (4 vCPU / 10.5 GiB, USD 157.68/month) are both returned with verified regional offering and price evidence, `m5a.xlarge` is recommended/cost-optimal under Balanced and Lowest Cost in the fixture. Values are based on the user-provided example and are not a live price quote.

## Agent response

- Comparison explanation is automatically triggered after a successful prompt comparison.
- LLM receives deterministic comparison evidence and has no tools in this explanatory call; it cannot change ranking, query other accounts or provision resources.
- Output is constrained to 6–8 short readable lines, with a bold recommendation first.
- Internal `<thinking>`, `<analysis>`, `<reasoning>`, `<scratchpad>` and `<chain_of_thought>` markup is sanitized before Markdown rendering.
- The deterministic fallback gives a useful evidence summary if the LLM explanation call fails.

## Comparison UI

- Provider cards identify the recommended and cost-optimal candidates.
- The candidate table includes Recommended / Optimal markers.
- The selector contains all returned priced USD configurations meeting both requested minimums.
- **Exact match check and closest configurations**, **Detailed comparison and exclusions**, and the all-discovered-options selector remain.
- AWS provisioning selection is enabled only for candidates with verified regional offering and adequate CPU/RAM. Azure/GCP are comparison candidates only until their reviewed Terraform provisioning stacks are implemented.

## Evidence and limitations

- AWS EC2 specifications and regional offerings are sourced from live APIs for the selected AWS connection; region offering does not confirm account quota or immediate spare capacity.
- Azure/GCP values remain public catalogue estimates unless connected account inventory APIs explicitly verify them.
- Monthly price is a compute-only estimate (730 hours) and excludes other costs unless explicitly priced.
- Latency is never inferred from region names; comparable measured latency is required.
- This is a source release. No AWS resource was created, no Terraform apply was executed, and no live EC2 deployment/browser test was performed by the build environment.

## Validation

- Pytest: 72 passed.
- Python compilation: passed.
- Shell syntax checks: passed.
- JSON parsing: passed.
- ZIP integrity: checked before release.
