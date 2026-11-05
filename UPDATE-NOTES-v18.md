# InfraMind v18 — fresh AWS Price List lookup and grounded Agent pricing

## Changes

- AWS On-Demand compute estimates now use AWS Price List Query API `AmazonEC2:GetProducts` filtered by instance type, AWS region code (with a location-name compatibility path), Linux, shared tenancy, pre-installed software and license model.
- Only On-Demand hourly (`Hrs`) USD dimensions are accepted. Ambiguous distinct price rates are rejected rather than guessed.
- Price results now include actual fetch time, effective date, publication date, product SKU, pricing endpoint region, model and cache status.
- Replaced indefinite `lru_cache` pricing with a bounded TTL cache; successful prices default to 300 seconds, and failures are never cached.
- Each new Agent comparison clears the Streamlit option cache and AWS Price List cache, forcing fresh AWS regional price requests for the shortlisted candidates.
- Added visible AWS Price List source and UTC retrieval timestamp to candidate comparison, plus an explicit unavailable warning when live AWS rates cannot be retrieved.
- Added `pricing:GetProducts` to the generated controller IAM policy and a minimal standalone `iam-live-pricing-policy.json` for the management-account EC2 role.
- Default AWS pricing shortlist is 12 candidates to bound API call count/latency; configurable with `CLOUDPILOT_AWS_DISCOVERY_CANDIDATES`.
- Agent evidence payload includes exact price, source, fetch timestamp, effective/publication dates and explicit public On-Demand assumptions.

## Important pricing interpretation

AWS public On-Demand list pricing is not an account-specific bill. Monthly numbers use 730 hours and exclude EBS, transfer, public IPv4, tax, support, Savings Plans, Reserved Instances, Spot and private discounts/credits. EC2 regional offerings are not a guarantee of account quota or immediate launch capacity. The UI must not invent a rate if the Price List API returns no unambiguous result.

## Validation

- Unit tests cover regionCode filtering, hourly price parsing, 730-hour monthly calculation, metadata, short TTL behavior, forced refresh, ambiguous-rate rejection and non-caching of failures.
- Full suite, compilation, shell checks, policy parsing and ZIP integrity are run before release.
- The build environment cannot call the user's authenticated AWS Pricing API or deploy the Docker image to EC2. Live AWS price verification must be run on the EC2 controller after attaching the included permission.
