#!/bin/bash
# build_mkp.sh - Build the netbird MKP locally from packages/netbird
#
# Unlike the cmk-wasabi-buckets script this does not copy files into a site's
# local/ tree first: an MKP is just a tar archive, so it is assembled here from
# the manifest. Install it on a site with `mkp add <file>` + `mkp enable netbird`
# and remove it again with `mkp remove netbird-<version>`.
#
# Usage:
#   ./build_mkp.sh [output_dir]        (default: current directory of the repo)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${1:-${SCRIPT_DIR}}"
mkdir -p "${OUT_DIR}"

python3 - "${SCRIPT_DIR}" "${OUT_DIR}" <<'PY'
import ast
import gzip
import io
import json
import sys
import tarfile
from pathlib import Path

root, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
manifest = ast.literal_eval((root / "packages" / "netbird").read_text())

# Manifest path -> file in this repository. The agent lives in netbird/libexec/
# but is installed as agents/special/agent_netbird.
def source_for(part: str, rel: str) -> Path:
    if part == "agents":
        return root / "netbird" / "libexec" / Path(rel).name
    return root / rel

def add_tar(outer: tarfile.TarFile, name: str, members: list[tuple[str, Path]]) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for arcname, path in members:
            info = tar.gettarinfo(str(path), arcname=arcname)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if path.name.startswith("agent_") else 0o644
            with path.open("rb") as handle:
                tar.addfile(info, handle)
    payload = gzip.compress(raw.getvalue(), mtime=0)
    info = tarfile.TarInfo(f"{name}.tar")
    info.size = len(payload)
    info.mtime = 0
    outer.addfile(info, io.BytesIO(payload))

def add_bytes(outer: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    outer.addfile(info, io.BytesIO(data))

target = out_dir / f"{manifest['name']}-{manifest['version']}.mkp"
with tarfile.open(target, "w") as outer:
    add_bytes(outer, "info", (repr(manifest) + "\n").encode())
    add_bytes(outer, "info.json", json.dumps(manifest).encode())
    for part, files in manifest["files"].items():
        members = []
        for rel in files:
            src = source_for(part, rel)
            if not src.is_file():
                sys.exit(f"ERROR: {rel} listed in the manifest but {src} does not exist")
            members.append((rel, src))
        add_tar(outer, part, members)
print(f"==> MKP ready: {target}")
PY
