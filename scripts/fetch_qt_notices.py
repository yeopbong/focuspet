from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import urllib.request

VERSION = "v6.8.3"
REPOSITORIES = ("pyside/pyside-setup", "qt/qtbase")
ROOT = Path(__file__).resolve().parents[1] / "docs" / "third-party" / "qt"


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "FocusPet-release-notices"})
    limit = 10_000_000 if url.startswith("https://api.github.com/") else 1_000_000
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Upstream notice or repository inventory exceeds its resource bound")
    return body


def collect(repo: str) -> dict:
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/{VERSION}?recursive=1"
    tree = json.loads(download(tree_url))
    commit = json.loads(download(f"https://api.github.com/repos/{repo}/commits/{VERSION}"))["sha"]
    files = {row["path"] for row in tree["tree"] if row["type"] == "blob"}
    selected = sorted(
        path for path in files if path.startswith("LICENSES/") or path.endswith("qt_attribution.json")
    )
    directory = ROOT / repo.split("/")[-1]
    manifest = []
    downloaded = {}

    def fetch(path):
        url = f"https://raw.githubusercontent.com/{repo}/{VERSION}/{path}"
        raw = download(url)
        return path, raw, url

    with ThreadPoolExecutor(max_workers=8) as pool:
        for path, raw, url in pool.map(fetch, selected):
            downloaded[path] = raw
            destination = directory / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            manifest.append(
                {
                    "path": str(destination.relative_to(ROOT)),
                    "source": url,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                }
            )

    references = set()
    unresolved = []
    for path, raw in downloaded.items():
        if not path.endswith("qt_attribution.json"):
            continue
        data = json.loads(raw, strict=False)
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            for name in str(entry.get("LicenseFile", "")).split():
                from posixpath import normpath

                resolved = normpath(str(PurePosixPath(path).parent / name))
                basename = PurePosixPath(resolved).name.lower()
                permitted = any(
                    word in basename
                    for word in (
                        "license",
                        "licence",
                        "copying",
                        "copyright",
                        "notice",
                        "readme",
                        "ftl",
                        "legal",
                        "psf",
                    )
                )
                if resolved in files and permitted and not resolved.startswith("../"):
                    references.add(resolved)
                elif name:
                    unresolved.append(
                        {
                            "attribution": path,
                            "component": entry.get("Name", entry.get("Id")),
                            "license_file_reference": name,
                            "reason": "Not a standalone license/notice text in this repository; original attribution and root SPDX text retained",
                        }
                    )
    remaining = sorted(references - downloaded.keys())
    with ThreadPoolExecutor(max_workers=8) as pool:
        for path, raw, url in pool.map(fetch, remaining):
            destination = directory / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            manifest.append(
                {
                    "path": str(destination.relative_to(ROOT)),
                    "source": url,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                }
            )
    return {
        "repository": repo,
        "tag": VERSION,
        "commit": commit,
        "tree_sha": tree["sha"],
        "files": manifest,
        "license_references_without_separate_copy": unresolved,
    }


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    result = [collect(repo) for repo in REPOSITORIES]
    (ROOT / "sources.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                entry["repository"]: {
                    "files": len(entry["files"]),
                    "unresolved_references": len(entry["license_references_without_separate_copy"]),
                    "commit": entry["commit"],
                }
                for entry in result
            },
            indent=2,
        )
    )
