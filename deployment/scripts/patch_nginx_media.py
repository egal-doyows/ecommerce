#!/usr/bin/env python3
"""
patch_nginx_media.py — one-shot, idempotent patch for the live nginx site.

Adds a nested `location ~* \.(jpg|jpeg|png|gif|webp|ico)$ { ... }` block
inside every `location /media/ { ... }` block in
/etc/nginx/sites-available/ecommerce, so safe raster images render inline
on the public site (instead of being force-downloaded by the strict
default headers added in the HR-uploads security commit).

Run as root:
    sudo python3 deployment/scripts/patch_nginx_media.py \
        && sudo nginx -t && sudo systemctl reload nginx

Re-running is safe — if the patch is already present, the script exits 0
without touching the file.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

CONF = Path('/etc/nginx/sites-available/ecommerce')

NESTED = """
        location ~* \\.(jpg|jpeg|png|gif|webp|ico)$ {
            add_header X-Content-Type-Options "nosniff" always;
            add_header Cache-Control "public" always;
            expires 30d;
        }
"""

SENTINEL = 'location ~* \\.(jpg|jpeg|png|gif|webp|ico)'


def patch(text: str) -> tuple[str, int]:
    """Insert NESTED just before the closing brace of every /media/ block."""
    out: list[str] = []
    i = 0
    n = 0
    while True:
        m = re.search(r'location\s+/media/\s*\{', text[i:])
        if not m:
            out.append(text[i:])
            return ''.join(out), n
        out.append(text[i : i + m.end()])
        depth, j = 1, i + m.end()
        while j < len(text) and depth > 0:
            ch = text[j]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            j += 1
        # j now points just past the matching closing brace
        body = text[i + m.end() : j - 1]
        out.append(body.rstrip() + '\n' + NESTED + '    }')
        i = j
        n += 1


def main() -> int:
    if not CONF.is_file():
        print(f'ERROR: {CONF} not found.', file=sys.stderr)
        return 1

    src = CONF.read_text()
    if SENTINEL in src:
        print('Already patched. Nothing to do.')
        return 0

    new, n = patch(src)
    if n == 0:
        print(f'ERROR: no `location /media/` block found in {CONF}.', file=sys.stderr)
        return 1

    backup = CONF.with_suffix(f'.bak.{int(time.time())}')
    backup.write_text(src)
    CONF.write_text(new)
    print(f'Patched {n} /media/ block(s). Backup written to {backup}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
