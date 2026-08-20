#!/usr/bin/env python3
"""Copy generated local assets into web/ and rewrite links for GitHub Pages."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
ASSET_ROOT = WEB_ROOT / "assets"
ATTRIBUTE_RE = re.compile(r'(?P<prefix>\b(?:href|src)=")(?P<url>[^"]+)(?P<suffix>")')
EMPTY_IMAGE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="


def rewrite_page(page: Path) -> tuple[int, int]:
    copied = 0
    rewritten = 0
    html = page.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        nonlocal copied, rewritten
        raw_url = match.group("url")
        parsed = urlsplit(raw_url)
        if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
            return match.group(0)

        source = (page.parent / unquote(parsed.path)).resolve()
        if source == WEB_ROOT or WEB_ROOT in source.parents:
            return match.group(0)
        if source != ROOT and ROOT not in source.parents:
            return match.group(0)
        if not source.is_file():
            if match.group("prefix").startswith('src="') and source.name == "image_contact_sheet.jpg":
                rewritten += 1
                return f'{match.group("prefix")}{EMPTY_IMAGE}{match.group("suffix")}'
            return match.group(0)

        destination = ASSET_ROOT / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1

        relative = Path(os.path.relpath(destination, page.parent)).as_posix()
        new_url = urlunsplit(("", "", relative, parsed.query, parsed.fragment))
        rewritten += 1
        return f'{match.group("prefix")}{new_url}{match.group("suffix")}'

    updated = ATTRIBUTE_RE.sub(replace, html)
    if updated != html:
        page.write_text(updated, encoding="utf-8")
    return copied, rewritten


def main() -> None:
    total_copied = 0
    total_rewritten = 0
    for page in sorted(WEB_ROOT.rglob("*.html")):
        copied, rewritten = rewrite_page(page)
        total_copied += copied
        total_rewritten += rewritten
    print(f"Prepared GitHub Pages site: {total_copied} assets copied, {total_rewritten} links rewritten")


if __name__ == "__main__":
    main()
