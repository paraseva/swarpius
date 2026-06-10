#!/usr/bin/env python3
"""Self-enforcing guards for publish-gated licence obligations.

These obligations apply once the repo publishes Docker images or serves the
frontend publicly. Rather than rely on remembering them, this guard trips the
moment the triggering condition appears in the repo:

  1. If any workflow gains an image-publish step (docker push / build-push), a
     container SBOM step (Syft/Tern/anchore) must exist — publishing an image
     redistributes its base-OS layer, which a language-level SBOM misses.
  2. The web-client attributions data (public/licenses.json) must exist — it is
     served to every browser and discharges the npm-prod attribution duty.

Run in CI (licenses workflow) and locally: `python3 licensing/guard_publish.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
ATTRIB_JSON = REPO / "web-client" / "public" / "licenses.json"

# An image is being published to a registry.
PUBLISH = re.compile(r"docker/build-push-action|docker\s+push|podman\s+push|push:\s*true", re.I)
# A container-level SBOM is being produced (covers the base-OS layer).
CONTAINER_SBOM = re.compile(r"anchore/sbom-action|\bsyft\b|\btern\b|cyclonedx.*image|# *license-guard: *container-sbom", re.I)


def main() -> int:
    failures: list[str] = []

    workflow_text = ""
    if WORKFLOWS.is_dir():
        for f in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow_text += f"\n# --- {f.name} ---\n" + f.read_text(encoding="utf-8", errors="replace")

    if PUBLISH.search(workflow_text) and not CONTAINER_SBOM.search(workflow_text):
        failures.append(
            "An image-publish step exists but no container SBOM step (Syft/Tern/"
            "anchore) was found. Publishing an image redistributes its base-OS "
            "layer — add a container-SBOM step, or annotate the SBOM step with "
            "'# license-guard: container-sbom'."
        )

    if not ATTRIB_JSON.exists():
        failures.append(
            f"{ATTRIB_JSON.relative_to(REPO)} is missing — the served frontend "
            "owes an open-source attributions list. Run "
            "`python3 licensing/licenses.py generate`."
        )

    if failures:
        print("✗ publish/SaaS licence guard failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✓ publish/SaaS licence guards satisfied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
