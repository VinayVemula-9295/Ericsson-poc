"""Render the checked-in walkthrough as a printable, standalone HTML document."""
import html
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "docs/application-flow-validation.md"
def inline(value):
    value = html.escape(value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
out = []
code, table, items = False, False, False
for line in source.read_text(encoding="utf-8").splitlines():
    if line.startswith("```"):
        out.append("</code></pre>" if code else "<pre><code>")
        code = not code
        continue
    if code:
        out.append(html.escape(line) + "\n")
        continue
    if line.startswith("| "):
        if re.fullmatch(r"[| :\-]+", line):
            continue
        if not table:
            out.append("<table>")
            table = True
            tag = "th"
        else:
            tag = "td"
        out.append("<tr>" + "".join(f"<{tag}>{inline(cell.strip())}</{tag}>" for cell in line.strip("|").split("|")) + "</tr>")
        continue
    if table:
        out.append("</table>")
        table = False
    if line.startswith("- "):
        if not items:
            out.append("<ul>")
            items = True
        out.append("<li>" + inline(line[2:]) + "</li>")
        continue
    if items:
        out.append("</ul>")
        items = False
    match = re.match(r"^(#{1,3}) (.*)", line)
    if match:
        level = len(match[1])
        out.append(f"<h{level}>{inline(match[2])}</h{level}>")
    elif line.strip():
        out.append("<p>" + inline(line) + "</p>")
if table:
    out.append("</table>")
if items:
    out.append("</ul>")
document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intelligent Provisioning Platform — flow validation</title><style>
body{font:15px/1.55 system-ui,sans-serif;color:#202b36;max-width:1120px;margin:36px auto;padding:0 24px}h1,h2{color:#244d72}h2{border-bottom:1px solid #ccd7e0;padding-top:20px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccd7e0;padding:10px;text-align:left;vertical-align:top}th{background:#edf3f8}code,pre{font-family:Consolas,monospace;background:#f3f5f7}code{overflow-wrap:anywhere}pre{padding:16px;white-space:pre-wrap}@media print{body{max-width:none;margin:0;font-size:11px}tr,pre{break-inside:avoid}h2{break-after:avoid}}
</style><body>''' + "\n".join(out) + "</body></html>"
source.with_suffix(".html").write_text(document, encoding="utf-8")
print("Generated docs/application-flow-validation.html")
