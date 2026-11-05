"""Verify provider-published image OS/architecture before saving a VM plan."""
import json
import re
import urllib.request
from provider_connections import get_aws_session, get_saved_secret

def verify(candidate, connection, image_id):
    os_name = candidate["os"]
    provider = candidate["provider"]
    meta = connection.get("metadata", {})
    if provider == "AWS":
        client = get_aws_session(candidate["connection_id"], purpose="inventory").client("ec2", region_name=candidate["region"])
        images = client.describe_images(Owners=["amazon"], ImageIds=[image_id])["Images"]
        if not images:
            raise ValueError("Image is not provider-published")
        image = images[0]
        windows = image.get("Platform") == "windows"
        if image.get("Architecture") != "x86_64" or image.get("State") != "available" or image.get("ProductCodes") or windows != (os_name == "Windows"):
            raise ValueError("Image OS, architecture, state or licensing is incompatible")
        name = image.get("Name", "")
        if os_name == "Linux" and not name.startswith("al2023-ami-") or os_name == "Windows" and not re.fullmatch(r"Windows_Server-.*-English-Full-Base-.*", name):
            raise ValueError("Select an Amazon Linux 2023 or Windows Server base image matching the price model")
    elif provider == "Azure":
        # Only provider platform image IDs; customer managed images are out of scope.
        pattern = r"/Subscriptions/[^/]+/Providers/Microsoft.Compute/Locations/([^/]+)/Publishers/(MicrosoftWindowsServer|Canonical)/ArtifactTypes/VMImage/Offers/[^/]+/Skus/[^/]+/Versions/[^/]+"
        match = re.fullmatch(pattern, image_id, re.I)
        if not match or match[1].lower() != candidate["region"].lower() or (match[2].lower() == "microsoftwindowsserver") != (os_name == "Windows"):
            raise ValueError("Use an approved provider platform image ID in the selected region")
        if os_name == "Linux" and any(term in image_id.lower() for term in ("pro-", "ubuntu-pro", "cis", "fips")):
            raise ValueError("Premium Linux licensing is outside the POC")
        from azure.identity import ClientSecretCredential
        secret = get_saved_secret(candidate["connection_id"])
        token = ClientSecretCredential(meta["tenant_id"], meta["client_id"], secret["client_secret"]).get_token("https://management.azure.com/.default").token
        url = "https://management.azure.com" + image_id + "?api-version=2024-03-01"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + token}), timeout=15) as response:
            image = json.load(response)
        props = image.get("properties", {})
        actual_os = str(props.get("osDiskImage", {}).get("operatingSystem", "")).lower()
        if actual_os != os_name.lower() or props.get("architecture", "x64") != "x64":
            raise ValueError("Azure image OS or architecture is incompatible")
    else:
        match = re.fullmatch(r"projects/(windows-cloud|ubuntu-os-cloud|debian-cloud|rocky-linux-cloud)/global/images/([a-z0-9-]+)", image_id)
        if not match or (match[1] == "windows-cloud") != (os_name == "Windows"):
            raise ValueError("Use a versioned provider-published GCP image path")
        if "pro-" in image_id.lower():
            raise ValueError("Premium Linux licensing is outside the POC")
        from google.cloud import compute_v1
        from google.oauth2 import service_account
        info = get_saved_secret(candidate["connection_id"])["service_account_json"]
        credentials = service_account.Credentials.from_service_account_info(json.loads(info) if isinstance(info, str) else info)
        image = compute_v1.ImagesClient(credentials=credentials).get(project=match[1], image=match[2])
        if image.status != "READY" or image.architecture != "X86_64":
            raise ValueError("GCP image is not ready or compatible")
    return {"os": os_name, "architecture": "x86_64", "image_id": image_id, "status": "verified"}
