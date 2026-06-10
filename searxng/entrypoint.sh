#!/bin/sh
# Swarpius wrapper around the SearXNG image's own entrypoint.
#
# SearXNG enables HTML output only by default. The agent's web_search
# tool needs the JSON endpoint, which requires `formats: [html, json]`
# under the `search:` block in settings.yml. Rather than redistribute
# a pre-modified copy of the upstream (AGPL-3.0) settings.yml, we let
# the image generate its own from the bundled template on first boot
# and then patch in the formats line idempotently.

set -eu

SETTINGS="${__SEARXNG_SETTINGS_PATH:-/etc/searxng/settings.yml}"
TEMPLATE=/usr/local/searxng/searx/settings.yml

if [ ! -f "$SETTINGS" ]; then
    /usr/local/searxng/.venv/bin/python3 - "$TEMPLATE" "$SETTINGS" <<'PY'
import secrets
import shutil
import sys
from pathlib import Path

src, dst = sys.argv[1], sys.argv[2]
shutil.copyfile(src, dst)
target = Path(dst)
target.write_text(
    target.read_text().replace("ultrasecretkey", secrets.token_urlsafe(32))
)
PY
fi

if ! grep -qE '^[[:space:]]+formats:[[:space:]]*\[.*\<json\>' "$SETTINGS"; then
    /usr/local/searxng/.venv/bin/python3 - "$SETTINGS" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text().splitlines()

search_idx = next(
    (i for i, line in enumerate(lines) if re.match(r"^search:\s*$", line)),
    None,
)
if search_idx is None:
    sys.exit(0)

block_end = len(lines)
for j in range(search_idx + 1, len(lines)):
    line = lines[j]
    if line and not line.startswith((" ", "\t", "#")):
        block_end = j
        break

formats_start = None
for j in range(search_idx + 1, block_end):
    if re.match(r"^  formats:", lines[j]):
        formats_start = j
        break

new_line = "  formats: [html, json]"
if formats_start is None:
    insert_at = block_end
    while insert_at > search_idx + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, new_line)
else:
    formats_end = formats_start + 1
    if not re.match(r"^  formats:\s*\[", lines[formats_start]):
        while formats_end < block_end and re.match(
            r"^    [-#]", lines[formats_end]
        ):
            formats_end += 1
    lines[formats_start:formats_end] = [new_line]

path.write_text("\n".join(lines) + "\n")
PY
fi

exec /usr/local/searxng/entrypoint.sh "$@"
