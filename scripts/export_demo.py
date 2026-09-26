"""
Export real agent runs from the local SQLite store into a static replay demo.

Judges need a public, login-free demo, but the agent runs on-device. This
script copies the UI into docs/ (served by GitHub Pages) together with
docs/demo-data.json: the full, unedited traces of visits that were run
locally against Gemma 4. In replay mode the UI reads that file instead of
the API and animates each recorded trace step by step.

Usage: python3 scripts/export_demo.py [encounter_id ...]   (default: all finished visits)
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sahayak import config, store  # noqa: E402

DOCS = ROOT / "docs"


def main(ids: list[str]) -> None:
    """Write docs/demo-data.json and copy the static UI into docs/."""
    if not ids:
        ids = [r["id"] for r in store.query(
            "SELECT id FROM encounters WHERE status IN ('done','handoff') ORDER BY created_at")]
    encounters = [store.get_encounter(i) for i in ids]
    visits = [{"id": e["id"], "status": e["status"], "triage": e["triage"], "degraded": e["degraded"],
               "created_at": e["created_at"], "name": e["patient"]["name"], "age_years": e["patient"]["age_years"]}
              for e in encounters]
    handoffs = [{**h, "name": e["patient"]["name"], "age_years": e["patient"]["age_years"]}
                for e in encounters for h in e["handoffs"]]
    DOCS.mkdir(exist_ok=True)
    for f in ("index.html", "app.js", "style.css"):
        shutil.copy(config.STATIC_DIR / f, DOCS / f)
    html = (DOCS / "index.html").read_text()
    html = html.replace('href="/style.css"', 'href="style.css"').replace(
        '<script src="/app.js"></script>', '<script>window.SAHAYAK_REPLAY = "demo-data.json";</script>\n<script src="app.js"></script>')
    (DOCS / "index.html").write_text(html)
    (DOCS / "demo-data.json").write_text(json.dumps(
        {"encounters": {e["id"]: e for e in encounters}, "visits": visits[::-1], "handoffs": handoffs},
        default=str, ensure_ascii=False, indent=1))
    print(f"exported {len(encounters)} visit(s) to {DOCS}")


if __name__ == "__main__":
    main(sys.argv[1:])
