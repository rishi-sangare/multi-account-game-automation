"""Make one copy of the ProTanki install per account, each with a unique Adobe AIR application id.

AIR runs only one instance per application id, so 10 accounts need 10 ids.

    python tools/clone_install.py --src "C:/Program Files (x86)/ProTanki Online" --dst C:/ProTanki --count 8

Creates C:/ProTanki/acc1 … acc8 and prints the exe path to put in each account. Safe to re-run.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


def find_descriptor(root: Path) -> Path | None:
    for cand in (root / "META-INF" / "AIR" / "application.xml", *root.rglob("application.xml")):
        if cand.exists():
            return cand
    return None


def clone(src: Path, dst: Path, acc_id: str) -> Path:
    target = dst / acc_id
    shutil.copytree(src, target, dirs_exist_ok=True)
    desc = find_descriptor(target)
    if desc is None:
        print(f"  ! {acc_id}: no application.xml found - not an AIR captive bundle? "
              "Try Sandboxie or attach mode instead.")
        return target
    text = desc.read_text(encoding="utf-8")
    m = re.search(r"<id>\s*([^<\s]+)\s*</id>", text)
    if not m:
        print(f"  ! {acc_id}: <id> element not found in {desc}")
        return target
    base = re.sub(r"\.acc[\w-]+$", "", m.group(1))
    new_id = f"{base}.{acc_id}"
    text = text[:m.start(1)] + new_id + text[m.end(1):]
    desc.write_text(text, encoding="utf-8")
    print(f"  {acc_id}: app id {new_id}")
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="installed ProTanki folder")
    ap.add_argument("--dst", required=True, help="folder that will hold acc1, acc2, …")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--prefix", default="acc")
    a = ap.parse_args()
    src, dst = Path(a.src), Path(a.dst)
    if not src.exists():
        print("source folder not found:", src)
        return 1
    exe = next((p.name for p in src.glob("*.exe") if not p.name.lower().startswith("unins")), "ProTanki.exe")
    dst.mkdir(parents=True, exist_ok=True)
    for i in range(1, a.count + 1):
        acc = f"{a.prefix}{i}"
        target = clone(src, dst, acc)
        print(f"     exe: {target / exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
