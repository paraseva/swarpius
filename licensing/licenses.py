#!/usr/bin/env python3
"""Resolve the included dependency closures, enforce the licence policy, and
regenerate the human-facing notices — all from one source of truth.

    python3 licensing/licenses.py check       # gate: nonzero exit on a policy violation
    python3 licensing/licenses.py generate     # rewrite THIRD_PARTY_NOTICES.md + attributions
    python3 licensing/licenses.py generate --sbom out/dir   # also emit CycloneDX SBOMs

Closures are computed self-contained (no network, no extra SBOM tool):
  - Python: walk installed metadata from agent/requirements-server.txt (the
    set frozen into the installer + agent Docker image). The dev venv only
    *adds* packages, so traversing from the server roots yields the included set.
  - npm: the production tree in web-client/package-lock.json (the closure that
    Vite bundles into the served frontend), licences read from node_modules.

Scope = what is actually redistributed. Dev deps are deliberately out of
scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

import importlib.metadata as md
from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parent.parent
POLICY_PATH = Path(__file__).resolve().parent / "policy.toml"
SERVER_REQS = REPO / "agent" / "requirements-server.txt"
NPM_LOCK = REPO / "web-client" / "package-lock.json"
NPM_MODULES = REPO / "web-client" / "node_modules"
NOTICES = REPO / "THIRD_PARTY_NOTICES.md"
ATTRIB_JSON = REPO / "web-client" / "public" / "licenses.json"
OVERRIDES = Path(__file__).resolve().parent / "overrides"

# Files whose verbatim contents discharge attribution (copyright + permission
# text, and Apache NOTICE files). Matched case-insensitively by filename.
LIC_FILE = re.compile(r"(licen[sc]e|copying|notice)", re.I)


def _read_override(name: str) -> list[tuple[str, str]]:
    """Vendored licence text for a package that includes none. Keyed by package
    name (normalised); the file is the verbatim text to reproduce."""
    for cand in (name, _norm(name)):
        path = OVERRIDES / f"{cand}.txt"
        if path.exists():
            return [(f"override:{path.name}", path.read_text(encoding="utf-8", errors="replace"))]
    return []

# ── Licence normalisation ────────────────────────────────────────────────────
# Map the many ways a licence is declared (PEP 639 expressions, classifier
# trove strings, free-text License fields, npm strings) onto SPDX ids.
_SPDX_MAP = {
    "mit": "MIT",
    "mit license": "MIT",
    "expat": "MIT",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "simplified bsd": "BSD-2-Clause",
    "apache": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "isc": "ISC",
    "isc license": "ISC",
    "isc license (iscl)": "ISC",
    "iscl": "ISC",
    "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "python software foundation license": "PSF-2.0",
    "python software foundation": "PSF-2.0",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python-2.0": "Python-2.0",
    "cnri-python": "CNRI-Python",
    "0bsd": "0BSD",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "ofl-1.1": "OFL-1.1",
    "ofl": "OFL-1.1",
    "sil ofl 1.1": "OFL-1.1",
    "sil open font license 1.1": "OFL-1.1",
}
# Splits a compound expression into its constituent licence tokens.
_SPLIT = (" and ", " or ", ";", "/", ",")


def to_spdx_ids(raw: str) -> list[str]:
    """Return the SPDX ids in a (possibly compound) licence string, or
    ['UNKNOWN:<raw>'] for any token that doesn't normalise."""
    text = (raw or "").strip().lower()  # lowercase first so AND/OR split case-insensitively
    if not text:
        return ["UNKNOWN:(none)"]
    tokens = [text]
    for sep in _SPLIT:
        tokens = [t for chunk in tokens for t in chunk.split(sep)]
    ids: list[str] = []
    for tok in tokens:
        key = tok.strip()
        if not key:
            continue
        mapped = _SPDX_MAP.get(key)
        ids.append(mapped if mapped else f"UNKNOWN:{key}")
    return ids or ["UNKNOWN:(none)"]


# ── Python closure ───────────────────────────────────────────────────────────
def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def _python_license(dist: md.Distribution) -> str:
    m = dist.metadata
    exp = m.get("License-Expression")
    if exp:
        return exp
    classifiers = [
        c.split("::")[-1].strip()
        for c in (m.get_all("Classifier") or [])
        if c.startswith("License")
    ]
    if classifiers:
        return " AND ".join(dict.fromkeys(classifiers))
    field = (m.get("License") or "").strip()
    if field and len(field) < 40 and "\n" not in field:
        return field
    return ""  # unresolved → exceptions table must cover it


def _python_texts(dist: md.Distribution) -> list[tuple[str, str]]:
    """Verbatim licence/notice files bundled in the dist-info."""
    texts = []
    for f in dist.files or []:
        s = str(f)
        if ".dist-info" in s and LIC_FILE.search(Path(s).name):
            try:
                texts.append((Path(s).name, Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace")))
            except (OSError, UnicodeError):
                # Skip an unreadable file and keep scanning; a package left with
                # no text at all is caught by the fail-closed gate, not silently
                # passed.
                continue
    return texts


def _python_url(dist: md.Distribution) -> str:
    m = dist.metadata
    for key in ("Home-page",):
        if m.get(key):
            return m[key]
    for entry in m.get_all("Project-URL") or []:
        # "Source, https://..." style
        parts = entry.split(",", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return ""


def python_components() -> list[dict]:
    direct = []
    for line in SERVER_REQS.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            direct.append(_norm(Requirement(line).name))
    seen: set[str] = set()
    stack = list(direct)
    while stack:
        name = _norm(stack.pop())
        if name in seen:
            continue
        seen.add(name)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        for raw in dist.requires or []:
            try:
                req = Requirement(raw)
            except Exception:
                continue
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            stack.append(req.name)
    comps = []
    for name in sorted(seen):
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        pkg = dist.metadata.get("Name") or name
        comps.append(
            {
                "ecosystem": "python",
                "name": pkg,
                "version": dist.version,
                "license": _python_license(dist),
                "url": _python_url(dist),
                "texts": _python_texts(dist) or _read_override(pkg),
            }
        )
    return comps


# ── npm closure (production only) ────────────────────────────────────────────
def _npm_license_from_modules(path: str) -> str:
    pkg = NPM_MODULES / path.removeprefix("node_modules/") / "package.json"
    if not pkg.exists():
        return ""
    try:
        data = json.loads(pkg.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    lic = data.get("license")
    if isinstance(lic, str):
        return lic
    if isinstance(lic, dict):
        return lic.get("type", "")
    licenses = data.get("licenses")
    if isinstance(licenses, list) and licenses:
        return " OR ".join(x.get("type", "") for x in licenses if isinstance(x, dict))
    return ""


def _npm_texts(path: str) -> list[tuple[str, str]]:
    """Verbatim licence/notice files in the package's node_modules dir."""
    pdir = NPM_MODULES / path.removeprefix("node_modules/")
    if not pdir.is_dir():
        return []
    texts = []
    for p in sorted(pdir.glob("*")):
        if p.is_file() and LIC_FILE.search(p.name):
            try:
                texts.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
            except (OSError, UnicodeError):
                # Skip an unreadable/undecodable file and keep scanning; a
                # package left with no text at all is caught by the fail-closed
                # gate, not silently passed.
                continue
    return texts


def npm_components() -> list[dict]:
    if not NPM_LOCK.exists():
        return []
    lock = json.loads(NPM_LOCK.read_text())
    out: dict[tuple, dict] = {}
    for path, meta in lock.get("packages", {}).items():
        if not path or "node_modules/" not in path:
            continue
        if meta.get("dev") or meta.get("devOptional"):
            continue
        name = path.split("node_modules/")[-1]
        version = meta.get("version", "")
        lic = meta.get("license") or _npm_license_from_modules(path)
        if isinstance(lic, dict):
            lic = lic.get("type", "")
        out[(name, version)] = {
            "ecosystem": "npm",
            "name": name,
            "version": version,
            "license": lic or "",
            "url": (meta.get("resolved") or "").split("/-/")[0].replace(
                "https://registry.npmjs.org", "https://www.npmjs.com/package"
            ),
            "texts": _npm_texts(path) or _read_override(name),
        }
    return [out[k] for k in sorted(out)]


# ── Policy gate ──────────────────────────────────────────────────────────────
def load_policy() -> dict:
    return tomllib.loads(POLICY_PATH.read_text())


def resolve_license(comp: dict, policy: dict) -> tuple[str, list[str]]:
    """Return (display_spdx, constituent_ids), applying exceptions."""
    exc = policy.get("exceptions", {}).get(comp["name"].lower()) or policy.get(
        "exceptions", {}
    ).get(comp["name"])
    if exc:
        return exc["spdx"], to_spdx_ids(exc["spdx"])
    ids = to_spdx_ids(comp["license"])
    display = " AND ".join(dict.fromkeys(i for i in ids if not i.startswith("UNKNOWN")))
    return (display or comp["license"] or "UNKNOWN"), ids


def evaluate(components: list[dict], policy: dict) -> list[dict]:
    allow = set(policy["allow"])
    deny = set(policy["deny"])
    violations = []
    for comp in components:
        def flag(why: str) -> None:
            violations.append({k: comp[k] for k in ("ecosystem", "name", "version")} | {"why": why})

        _, ids = resolve_license(comp, policy)
        reason = None
        for i in ids:
            if i in deny:
                reason = f"{i}: DENIED (copyleft/commercial-hostile)"
            elif i.startswith("UNKNOWN"):
                reason = "UNRECOGNISED licence — add to allow-list or [exceptions]"
            elif i not in allow:
                reason = f"{i} not in allow-list"
            if reason:
                break
        if reason:
            flag(reason)
            continue
        # Attribution completeness: every included component must reproduce its
        # licence/copyright text. No bundled file and no override → hard fail
        # (no silent gaps). Supply the text via licensing/overrides/<name>.txt.
        if not comp.get("texts"):
            flag("no bundled LICENSE/NOTICE text — add licensing/overrides/<name>.txt")
    return violations


# ── Renderers ────────────────────────────────────────────────────────────────
def render_sbom(components: list[dict], policy: dict, ecosystem: str) -> dict:
    comps = [c for c in components if c["ecosystem"] == ecosystem]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [
            {
                "type": "library",
                "name": c["name"],
                "version": c["version"],
                "licenses": [{"expression": resolve_license(c, policy)[0]}],
            }
            for c in comps
        ],
    }


def render_attributions(components: list[dict], policy: dict) -> dict:
    npm = [c for c in components if c["ecosystem"] == "npm"]
    return {
        "generated_by": "licensing/licenses.py",
        "scope": "web-client production dependencies (served to the browser)",
        "components": [
            {
                "name": c["name"],
                "version": c["version"],
                "license": resolve_license(c, policy)[0],
                "url": c["url"],
            }
            for c in npm
        ],
    }


def render_notices(components: list[dict], policy: dict) -> str:
    py = [c for c in components if c["ecosystem"] == "python"]
    js = [c for c in components if c["ecosystem"] == "npm"]
    from collections import Counter

    counts = Counter(resolve_license(c, policy)[0] for c in components)
    lines = [
        "# Third-Party Notices",
        "",
        "Generated by `licensing/licenses.py` — **do not edit by hand**; run `python3 licensing/licenses.py generate` and commit.",
        "",
        "Covers the third-party software Swarpius **redistributes** in a built artefact: the agent's runtime closure (resolved from `agent/requirements-server.txt` — the set frozen into the installer and the agent Docker image) and the web-client **production** closure (bundled into the served frontend). Dependencies a user installs themselves from source (`requirements.txt` dev/CLI extras, `npm install` devDependencies) are fetched directly from PyPI/npm and are not redistributed here.",
        "",
        "## Summary",
        "",
        "| Licence | Count |",
        "|---|---|",
    ]
    for lic, n in counts.most_common():
        lines.append(f"| {lic} | {n} |")
    lines += [
        "",
        f"**Total components: {len(components)}** ({len(py)} Python, {len(js)} JavaScript)",
        "",
    ]

    def section(title: str, comps: list[dict], scope: str) -> list[str]:
        out = [f"## {title}", "", scope, ""]
        for c in comps:
            out.append(f"### {c['name']} {c['version']}")
            out.append("")
            out.append(f"- **License:** {resolve_license(c, policy)[0]}")
            if c["url"]:
                out.append(f"- **Source:** {c['url']}")
            out.append("")
        return out

    lines += section(
        "Python (Agent)", py,
        "Resolved closure of `agent/requirements-server.txt`.",
    )
    lines += section(
        "JavaScript (Web Client production dependencies)", js,
        "Production closure of `web-client/package.json` (devDependencies excluded).",
    )
    # Verbatim licence/notice texts, deduplicated by content (identical texts —
    # e.g. the shared Apache-2.0 text — are grouped under all the packages that
    # carry them). This is what actually discharges the MIT/BSD/ISC copyright
    # reproduction and the Apache-2.0 §4(d) NOTICE obligations.
    lines += [
        "## Appendix: Licence & Notice Texts",
        "",
        "Reproduced verbatim as bundled with each component. Byte-identical texts are grouped under the packages that share them.",
        "",
    ]
    groups: dict[str, list] = {}
    for c in components:
        label = f"{c['name']} {c['version']}"
        for fname, text in c.get("texts", []):
            digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
            grp = groups.setdefault(digest, [text, set()])
            grp[1].add(f"{label} ({fname})")
    for text, labels in sorted(groups.values(), key=lambda g: sorted(g[1])[0].lower()):
        lines.append("### " + "; ".join(sorted(labels)))
        lines.append("")
        lines.append("```text")
        lines.append(text.rstrip("\n"))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


# ── Commands ─────────────────────────────────────────────────────────────────
def cmd_check(args) -> int:
    policy = load_policy()
    components = python_components() + npm_components()
    violations = evaluate(components, policy)
    print(f"Scanned {len(components)} included components "
          f"({sum(c['ecosystem']=='python' for c in components)} Python, "
          f"{sum(c['ecosystem']=='npm' for c in components)} npm).")
    if violations:
        print(f"\n✗ {len(violations)} licence policy violation(s):\n")
        for v in violations:
            print(f"  {v['ecosystem']}: {v['name']} {v['version']} — {v['why']}")
        print("\nFix: add an allowed SPDX id to licensing/policy.toml, or a "
              "reviewed [exceptions] entry, or remove the dependency.")
        return 1
    print("✓ all included components satisfy the licence policy.")
    return 0


def cmd_generate(args) -> int:
    policy = load_policy()
    components = python_components() + npm_components()
    NOTICES.write_text(render_notices(components, policy))
    ATTRIB_JSON.parent.mkdir(parents=True, exist_ok=True)
    ATTRIB_JSON.write_text(json.dumps(render_attributions(components, policy), indent=2) + "\n")
    print(f"wrote {NOTICES.relative_to(REPO)} and {ATTRIB_JSON.relative_to(REPO)}")
    if args.sbom:
        out = Path(args.sbom)
        out.mkdir(parents=True, exist_ok=True)
        (out / "agent.cdx.json").write_text(
            json.dumps(render_sbom(components, policy, "python"), indent=2) + "\n")
        (out / "web-client.cdx.json").write_text(
            json.dumps(render_sbom(components, policy, "npm"), indent=2) + "\n")
        print(f"wrote CycloneDX SBOMs to {out}/")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="enforce the licence policy (nonzero exit on violation)")
    g = sub.add_parser("generate", help="rewrite notices + attributions")
    g.add_argument("--sbom", metavar="DIR", help="also emit CycloneDX SBOMs to DIR")
    args = ap.parse_args()
    return {"check": cmd_check, "generate": cmd_generate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
