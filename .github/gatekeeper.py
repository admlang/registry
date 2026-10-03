#!/usr/bin/env python3
"""Validates one registry pull request. Exits non-zero with the reason on stderr.

Environment: GH_TOKEN, REPO (owner/name of this registry), PR_NUMBER, PR_AUTHOR,
BASE_SHA, HEAD_SHA. Needs `gh`, `git` and `adm` on PATH.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

RESERVED = {"std", "builtin"}
REQUIRED = ["namespace", "name", "version", "hash", "publisher", "repo", "description", "date", "adm", "yanked"]
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
NAMESPACE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def die(msg):
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def sh(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def gh_api(path, **params):
    args = ["gh", "api", path]
    for k, v in params.items():
        args += ["-f", f"{k}={v}"]
    return json.loads(sh(*args))


def index_file(namespace):
    first = namespace[0].lower()
    return f"index/{first}" if first.isalpha() else "index/0-9"


def semver_key(v):
    m = SEMVER_RE.match(v)
    if not m:
        die(f"version {v!r} is not semver")
    major, minor, patch, pre = m.groups()
    # A prerelease sorts before the release it precedes.
    pre_key = (1,) if pre is None else (0, tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")))
    return (int(major), int(minor), int(patch), pre_key)


def main():
    repo, pr, author = os.environ["REPO"], os.environ["PR_NUMBER"], os.environ["PR_AUTHOR"]
    base, head = os.environ["BASE_SHA"], os.environ["HEAD_SHA"]

    # 1. exactly one file touched, exactly one line appended at its end
    sh("git", "fetch", "--quiet", "origin", head)
    files = sh("git", "diff", "--name-only", base, head).split()
    if len(files) != 1 or not files[0].startswith("index/"):
        die(f"a submission changes exactly one index file, this one changes {files}")
    path = files[0]
    diff = sh("git", "diff", "--unified=0", base, head, "--", path).splitlines()
    added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    if removed or len(added) != 1:
        die("the index is append-only: add exactly one line, change nothing else")
    old = sh("git", "show", f"{base}:{path}") if sh("git", "ls-tree", base, "--", path).strip() else ""
    new = sh("git", "show", f"{head}:{path}")
    if not new.startswith(old) or not new.endswith("\n"):
        die("the new line must be appended at the end of the file, terminated by a newline")

    try:
        line = json.loads(added[0])
    except ValueError as e:
        die(f"the added line is not JSON: {e}")
    missing = [k for k in REQUIRED if k not in line]
    if missing:
        die(f"missing fields: {missing}")
    ns, name, version = line["namespace"], line["name"], line["version"]
    if not NAMESPACE_RE.match(ns):
        die(f"namespace {ns!r} is not a GitHub account name")
    if not NAME_RE.match(name) or name in RESERVED:
        die(f"name {name!r} is reserved or not a module name")
    if path != index_file(ns):
        die(f"namespace {ns} belongs in {index_file(ns)}, not {path}")
    if line["yanked"] not in (True, False):
        die("yanked must be true or false")

    # 2. ownership: the author is the namespace or a member of that organisation,
    #    and the repository belongs to the namespace
    if author.lower() != ns.lower():
        try:
            gh_api(f"orgs/{ns}/members/{author}")
        except subprocess.CalledProcessError:
            die(f"{author} is neither the account {ns} nor a member of that organisation")
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", line["repo"])
    if not m or m.group(1).lower() != ns.lower():
        die(f"repo {line['repo']} does not belong to {ns}")
    repo_name = m.group(2)

    # 5. version order among existing lines of this namespace/name
    for prev in old.splitlines():
        if not prev.strip():
            continue
        p = json.loads(prev)
        if p["namespace"].lower() == ns.lower() and p["name"] == name:
            if line["yanked"]:
                continue  # a yank line repeats an existing version
            if semver_key(p["version"]) >= semver_key(version):
                die(f"version {version} is not newer than the listed {p['version']}")
    if line["yanked"]:
        listed = any(json.loads(l)["namespace"].lower() == ns.lower() and json.loads(l)["name"] == name
                     and json.loads(l)["version"] == version for l in old.splitlines() if l.strip())
        if not listed:
            die(f"cannot yank {name} {version}: it is not listed")
        print(f"yank of {ns}:{name} {version} accepted")
        return

    # 3. fetch the container: the release asset of tag <name>@<version> when the
    #    line names one, else dist/<name>-<version>.admlib on tag v<version>
    with tempfile.TemporaryDirectory() as tmp:
        tag = line.get("tag") or f"v{version}"
        if "tag" in line and tag != f"{name}@{version}":
            die(f"tag {tag!r} must be {name}@{version}")
        file = f"{name}-{version}.admlib"
        if line.get("asset"):
            if "tag" not in line:
                die("a line with an asset names its tag")
            want = f"https://github.com/{ns}/{repo_name}/releases/download/{urllib.parse.quote(tag, safe='@')}/{file}"
            if line["asset"] != want:
                die(f"asset {line['asset']} must be the release asset {want}")
            try:
                release = gh_api(f"repos/{ns}/{repo_name}/releases/tags/{urllib.parse.quote(tag, safe='')}")
            except subprocess.CalledProcessError:
                die(f"{ns}/{repo_name} has no release for tag {tag}")
            if file not in [a["name"] for a in release.get("assets", [])]:
                die(f"the release {tag} of {ns}/{repo_name} has no asset {file}")
            container = os.path.join(tmp, file)
            try:
                with urllib.request.urlopen(line["asset"]) as resp, open(container, "wb") as out:
                    out.write(resp.read())
            except OSError as e:
                die(f"cannot download {line['asset']}: {e}")
        else:
            url = f"https://github.com/{ns}/{repo_name}.git"
            try:
                sh("git", "clone", "--quiet", "--depth", "1", "--branch", tag, url, tmp)
            except subprocess.CalledProcessError as e:
                die(f"cannot fetch tag {tag} from {url}: {e.stderr.strip()}")
            container = os.path.join(tmp, "dist", file)
            if not os.path.isfile(container):
                die(f"tag {tag} of {url} has no dist/{file}")
        digest = "sha256:" + hashlib.sha256(open(container, "rb").read()).hexdigest()
        if digest != line["hash"]:
            die(f"hash mismatch: the index says {line['hash']}, the container is {digest}")

        # 4. checksums, signature, manifest and publisher through the compiler itself
        try:
            info = json.loads(sh("adm", "lib", "verify", "--json", container))
        except subprocess.CalledProcessError as e:
            die(f"adm lib verify rejected the container: {e.stderr.strip() or e.stdout.strip()}")
        if info["name"] != name or info["version"] != version:
            die(f"the container is {info['name']} {info['version']}, the line says {name} {version}")
        if not info.get("signed") or info.get("publisher") != line["publisher"]:
            die(f"the container's publisher {info.get('publisher')} does not match the line's {line['publisher']}")
        for mod in info.get("modules", []):
            if mod != name and not mod.startswith(name + "."):
                die(f"module {mod} is outside the package's prefix {name}")

    print(f"{ns}:{name} {version} verified (publisher {line['publisher']})")


if __name__ == "__main__":
    main()
