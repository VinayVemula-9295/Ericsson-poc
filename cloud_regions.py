"""Public, official commercial-cloud region catalogues; no account credentials."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
import urllib.request

import streamlit as st

SOURCES = {
    "AWS": "https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html",
    "Azure": "https://learn.microsoft.com/en-us/azure/reliability/regions-list",
    "GCP": "https://docs.cloud.google.com/compute/docs/regions-zones",
}


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.rows, self.row, self.cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.rows = []
        elif tag == "tr" and self.rows is not None:
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append(self.rows)
            self.rows = None


def parse_catalogue(provider, html):
    parser = _Tables()
    parser.feed(html)
    regions = {}
    for table in parser.tables:
        if not table:
            continue
        headers = [c.lower() for c in table[0]]
        expected = {"AWS": ("code", "name"), "Azure": ("programmatic name", "region"),
                    "GCP": ("zones", "location")}[provider]
        if not all(h in headers for h in expected):
            continue
        code_index, label_index = (headers.index(h) for h in expected)
        for row in table[1:]:
            if len(row) <= max(code_index, label_index):
                continue
            code, label = row[code_index].strip(), row[label_index].strip()
            if not label or "coming soon" in " ".join(row).lower() or "announced" in " ".join(row).lower():
                continue
            zone = None
            if provider == "AWS":
                if not re.fullmatch(r"(?:af|ap|ca|eu|il|me|mx|sa|us)-(?!gov-)[a-z]+-\d+", code):
                    continue
            elif provider == "Azure":
                if not re.fullmatch(r"[a-z]+[a-z0-9]*", code):
                    continue
            else:
                if not re.fullmatch(r"[a-z]+-[a-z]+\d+-[a-z]", code):
                    continue
                zone, code = code, code.rsplit("-", 1)[0]
            entry = regions.setdefault(code, dict(value=code, label=f"{label} ({code})", zones=[]))
            if zone and zone not in entry["zones"]:
                entry["zones"].append(zone)
    if not regions:
        raise ValueError("Official region table was unavailable or unrecognised")
    for row in regions.values():
        row["zones"].sort()
    return sorted(regions.values(), key=lambda row: row["label"].casefold())


@st.cache_data(ttl=21600, show_spinner=False)
def load_catalogue(provider):
    """Cache successful reads for six hours; failures never become region choices."""
    url = SOURCES[provider]
    request = urllib.request.Request(url, headers={"User-Agent": "IntelligentProvisioningPlatform/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError("Official region catalogue unavailable")
        raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("Official region catalogue exceeded size limit")
    rows = parse_catalogue(provider, raw.decode("utf-8"))
    return dict(regions=rows, source=url, retrieved_at=datetime.now(timezone.utc).isoformat())
