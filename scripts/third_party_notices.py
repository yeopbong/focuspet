"""Collect installed distribution license notices, preserving their text."""

from importlib.metadata import distributions
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "docs" / "third-party"
root.mkdir(parents=True, exist_ok=True)
rows = []
for dist in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
    name = dist.metadata["Name"]
    if name.lower() == "focuspet":
        continue
    folder = root / name
    copied = []
    for file in dist.files or []:
        base = Path(str(file)).name.lower()
        if any(base.startswith(p) for p in ("license", "copying", "notice")) and "dist-info" in str(file):
            source = Path(dist.locate_file(file))
            if source.is_file() and source.stat().st_size < 1000000:
                folder.mkdir(exist_ok=True)
                target = folder / Path(str(file)).name
                target.write_bytes(source.read_bytes())
                copied.append(str(target.relative_to(root)))
    license_name = (
        dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See distribution"
    ).splitlines()[0][:100]
    rows.append(
        f"| {name} | {dist.version} | {license_name} | "
        + (", ".join(f"[{Path(x).name}]({x})" for x in copied) or "Distribution metadata / upstream")
        + " |"
    )
text = (
    "# Third-party components\n\nThe application source is MIT. Bundled components retain their own licenses. Runtime and development dependencies below reflect the measured environment. Qt/PySide includes LGPL/GPL/commercial licensing options; distribution must preserve applicable notices and allow the rights required by those licenses. Dynamic Qt libraries remain separate files in the bundle; source rebuild instructions are provided in the project. No claim of exclusive authorship is made for third-party code.\n\n| Component | Version | Declared license | Preserved notices |\n|---|---|---|---|\n"
    + "\n".join(rows)
    + "\n"
)
text += "\nAdditional preserved notices: [Qt / Qt for Python 6.8.3](qt/README.md), [Python runtime](Python/LICENSE.txt).\n"
(root / "README.md").write_text(text)
