"""Write docs/openapi.json and the endpoint table in docs/API.md from the live app.

    python -m scripts.export_openapi
"""
import json
import os
import re
from pathlib import Path

os.environ.setdefault("ENVIRONMENT", "development")
from app.main import create_app  # noqa: E402

DOCS = Path(__file__).resolve().parents[2] / "docs"
spec = create_app().openapi()
(DOCS / "openapi.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

rows = {}
for path, methods in spec["paths"].items():
    for method, op in methods.items():
        tag = (op.get("tags") or ["other"])[0]
        rows.setdefault(tag, []).append(f"| {method.upper()} | `{path.removeprefix('/api/v1')}` | {op.get('summary', '')} |")
order = ["health", "me", "firms", "members", "taxpayers", "documents", "exports", "dashboard", "admin", "files"]
out = ["<!-- BEGIN GENERATED ENDPOINTS (scripts/export_openapi.py) -->",
       f"_{sum(len(v) for v in rows.values())} endpoints, generated from the FastAPI app; full schemas in "
       "[openapi.json](openapi.json)._", ""]
for tag in sorted(rows, key=lambda t: order.index(t) if t in order else 99):
    out += [f"### {tag}", "", "| Method | Path | Summary |", "|---|---|---|", *rows[tag], ""]
out.append("<!-- END GENERATED ENDPOINTS -->")
api_md = (DOCS / "API.md").read_text(encoding="utf-8")
block = "\n".join(out)
if "BEGIN GENERATED ENDPOINTS" in api_md:
    api_md = re.sub(r"<!-- BEGIN GENERATED ENDPOINTS.*?END GENERATED ENDPOINTS -->", block, api_md, flags=re.S)
else:
    api_md = api_md.split("## Endpoints (Milestone 1)")[0] + "## Endpoints\n\n" + block + "\n"
(DOCS / "API.md").write_text(api_md, encoding="utf-8")
print(f"wrote {sum(len(v) for v in rows.values())} endpoints")
