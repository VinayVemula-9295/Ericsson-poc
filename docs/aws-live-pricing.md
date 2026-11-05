# Live AWS EC2 price lookup

Intelligent Provisioning Platform retrieves AWS Linux/UNIX shared-tenancy On-Demand compute prices through the **AWS Price List Query API** (`AmazonEC2:GetProducts`). It does not use fixture prices or fall back to prices copied from screenshots. EC2 instance CPU/RAM and regional offering data are fetched independently from the selected Demo/Test account role.

## Request behavior

- Every new **Compare clouds and recommend** submission clears the application result cache and the underlying AWS Price List cache, then queries fresh AWS pricing for the shortlisted EC2 types in the requested region.
- A successful low-level price result may be reused for up to `CLOUDPILOT_PRICE_CACHE_TTL_SECONDS` within the process. The default is 300 seconds. The explicit Compare action clears this cache first. Failed/ambiguous price requests are not cached.
- The default AWS discovery shortlist is 12 closest compatible types (`CLOUDPILOT_AWS_DISCOVERY_CANDIDATES=12`) to bound Price List API calls. Set a higher value only after checking request latency and API throttling.
- The result records the price retrieval timestamp, effective date, publication date, Price List product SKU, query endpoint region, cache status and price model.
- If the API permission is missing, endpoint is unsupported, no exact product rate exists, or multiple distinct rates remain after filtering, the candidate price is marked unavailable. Intelligent Provisioning Platform does not substitute example/sample prices.

## Price assumptions

- Standard On-Demand, Linux, shared tenancy, EC2 compute only.
- Monthly estimate = hourly USD rate × 730 hours.
- Excludes EBS volumes/snapshots, data transfer, public IPv4, taxes, support, Savings Plans, Reserved Instance benefits, Spot pricing and account-specific private discounts/credits.
- Price List rates are public list rates for informational estimation. They are not an account-specific final bill or a guarantee of the amount charged.
- `DescribeInstanceTypeOfferings` confirms regional offering, not an account's vCPU quota or immediate spare capacity.

AWS documentation: [Price List Query API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-price-list-query-api.html), [GetProducts API](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html), [AWS Price List endpoints](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html).

## Required controller permission

The Price List call uses the **Intelligent Provisioning Platform EC2 controller role** in the management account (`704298683641`), not the target inventory role in Demo or Test. Attach the included `iam-live-pricing-policy.json` to `CloudPilotReadTargetMetricsRole` (or include the same statement in the generated controller policy):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadLivePublicEC2OnDemandPrices",
      "Effect": "Allow",
      "Action": ["pricing:GetProducts"],
      "Resource": "*"
    }
  ]
}
```

No pricing permission needs to be added to `IIPPInventoryReadRole` unless the application is deliberately changed to call the Price List API using the target-account assumed role. The current design uses the controller identity for the public price lookup and the selected target role for EC2 inventory/offerings.

The Price List Query API endpoints currently configured/supported by this package are `us-east-1`, `ap-south-1`, and `eu-central-1`; `AWS_PRICING_API_REGION` selects the API endpoint region and is separate from the target EC2 region.

## Verify from the EC2 host

After attaching the policy in Naresh-Acc, run this read-only check on the EC2 controller:

```bash
python3 - <<'PY'
from aws_data import clear_price_cache, get_ec2_instance_on_demand_price
clear_price_cache()
for instance_type in ("m5a.xlarge", "c5n.xlarge"):
    result = get_ec2_instance_on_demand_price(
        instance_type, platform_details="Linux/UNIX", tenancy="default", region="ap-south-1"
    )
    print({k: result.get(k) for k in (
        "available", "instance_type", "region", "hourly_usd", "monthly_730h_usd",
        "price_source", "retrieved_at", "effective_date", "publication_date",
        "pricing_api_region", "cache_status", "error"
    )})
PY
```

Expected success fields include `available=True`, an hourly rate, `monthly_730h_usd`, source `AWS Price List Query API · AmazonEC2 GetProducts`, a recent `retrieved_at`, and `cache_status=MISS` on the first call after clearing. Actual rates are retrieved when this command runs and are not embedded in the release.

## How Agent uses the result

The deterministic comparison receives the live AWS rates and the fresh EC2 hardware/region evidence before ranking. The Agent explanation is called after comparison and receives those exact returned values, source and timestamp. The LLM may explain the evidence but cannot change the deterministic ranking or invent a price. If AWS pricing is unavailable, the UI explains the missing permission/API evidence and leaves AWS price-based recommendation unavailable rather than using static numbers.
