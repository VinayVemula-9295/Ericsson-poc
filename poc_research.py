"""Bounded official documentation retrieval; content is evidence, never instructions."""
import re
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
import time

URLS = (
    "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/AMIs.html",
    "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview",
    "https://cloud.google.com/compute/docs/machine-resource",
    "https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html",
    "https://learn.microsoft.com/en-us/azure/aks/supported-kubernetes-versions",
    "https://cloud.google.com/kubernetes-engine/docs/release-schedule",
)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Documentation redirect requires reviewed source URL")

@lru_cache(maxsize=4)
def _fetch(bucket):
    results = []
    opener = urllib.request.build_opener(NoRedirect)
    for url in URLS:
        row = {"source": url, "retrieved_at": datetime.now(timezone.utc).isoformat()}
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "IIPP-Research/1.0"}), timeout=8) as response:
                html = response.read(512000).decode("utf-8", errors="replace")
            html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
            row.update(status="retrieved", excerpt=re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", html))[:3000],
                       verification="Reference only; not verified capacity or policy evidence")
        except Exception:
            row["status"] = "unavailable"
        results.append(row)
    return results

def research():
    return _fetch(int(time.time() // 86400))
