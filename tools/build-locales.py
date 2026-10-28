"""Regenerate the browser bundle after editing public/locales.json."""
import json
from pathlib import Path

public = Path(__file__).resolve().parents[1] / "public"
catalog = json.loads((public / "locales.json").read_text(encoding="utf-8"))
assert set(catalog) == {"zh-CN", "en", "ja", "ko", "ms"}
assert all(set(entries) == set(catalog["en"]) for entries in catalog.values())
(public / "locales.js").write_text(
    "window.NorthstarMessages = " + json.dumps(catalog, ensure_ascii=False, indent=2) + ";\n",
    encoding="utf-8",
)
