"""Build the image, inventory its runtime, run the checks against it, clean up.

Standard library only. Every command writes its evidence under evidence/<label>/ so a
reviewer can tie the runtime inventory and the integration checks to the same image
ID and the same commit.

    python tools/evaluate.py build  [--label NAME] [--tag TAG] [--platform linux/amd64]
    python tools/evaluate.py check  [--label NAME] [--tag TAG] [--port 8080]
    python tools/evaluate.py clean  [--tag TAG]

`build` records: commit SHA (and whether the tree was dirty), the runtime image the
Dockerfile names, the digest that tag resolved to for the selected platform, the final
image ID, and `dotnet --list-runtimes` run INSIDE the final image.

`check` starts the final image bound to loopback, runs tests/ against it, stops and
removes the container (also on failure), and records the test output next to the
image ID it ran against. Its exit code is the test result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TAG = "cookie-demo:local"
DEFAULT_CONTAINER = "cookie-demo"
DEFAULT_PLATFORM = "linux/amd64"


def run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, cwd=ROOT)


def git(*args: str) -> str:
    try:
        return run(["git", *args]).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def evidence_dir(label: str) -> str:
    path = os.path.join(ROOT, "evidence", label)
    os.makedirs(path, exist_ok=True)
    return path


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    print(f"wrote {os.path.relpath(path, ROOT)}")


def dockerfile_images() -> dict[str, str]:
    """The SDK and runtime images the Dockerfile names (ARG defaults)."""
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
        text = fh.read()
    found = dict(re.findall(r"^ARG\s+(SDK_IMAGE|RUNTIME_IMAGE)=(\S+)", text, flags=re.M))
    missing = {"SDK_IMAGE", "RUNTIME_IMAGE"} - set(found)
    if missing:
        raise SystemExit(f"Dockerfile does not declare ARG {', '.join(sorted(missing))}")
    return found


def resolve_platform_digest(image: str, platform: str) -> dict:
    """The digest the tag resolves to for one platform, straight from the registry."""
    out = run(["docker", "manifest", "inspect", "--verbose", image]).stdout
    data = json.loads(out)
    entries = data if isinstance(data, list) else [data]
    os_name, arch = platform.split("/", 1)
    for entry in entries:
        desc = entry.get("Descriptor", {})
        plat = desc.get("platform", {})
        if plat.get("os") == os_name and plat.get("architecture") == arch:
            return {"platform": platform, "digest": desc.get("digest"), "ref": entry.get("Ref")}
    raise SystemExit(f"{image} has no manifest for {platform}")


def inspect(image_or_container: str, fmt: str) -> str:
    return run(["docker", "inspect", "--format", fmt, image_or_container]).stdout.strip()


def cmd_build(args: argparse.Namespace) -> int:
    images = dockerfile_images()
    runtime_image = images["RUNTIME_IMAGE"]
    resolved = resolve_platform_digest(runtime_image, args.platform)

    run(["docker", "build", "--platform", args.platform, "-t", args.tag, "."], capture=False)

    image_id = inspect(args.tag, "{{.Id}}")
    runtimes = run(["docker", "run", "--rm", "--platform", args.platform, "--entrypoint", "dotnet", args.tag, "--list-runtimes"]).stdout

    record = {
        "label": args.label,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "commit": git("rev-parse", "HEAD"),
        "tree_dirty": bool(git("status", "--porcelain")),
        "platform": args.platform,
        "sdk_image": images["SDK_IMAGE"],
        "runtime_image": runtime_image,
        "runtime_image_platform_digest": resolved["digest"],
        "image_tag": args.tag,
        "image_id": image_id,
        "list_runtimes": runtimes.strip().splitlines(),
    }
    out = evidence_dir(args.label)
    write(os.path.join(out, "build.json"), json.dumps(record, indent=2))
    write(os.path.join(out, "list-runtimes.txt"), runtimes)
    print()
    print(f"image {args.tag} = {image_id}")
    print(f"base  {runtime_image} ({args.platform}) = {resolved['digest']}")
    print("runtimes inside the final image:")
    print(runtimes)
    return 0


def wait_for(url: str, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
                last = f"HTTP {resp.status}"
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
        time.sleep(0.5)
    raise SystemExit(f"{url} did not come up within {seconds}s (last: {last})")


def cmd_check(args: argparse.Namespace) -> int:
    image_id = inspect(args.tag, "{{.Id}}")
    base = f"http://127.0.0.1:{args.port}"
    out = evidence_dir(args.label)

    subprocess.run(["docker", "rm", "-f", args.container], capture_output=True, cwd=ROOT)
    run(
        [
            "docker", "run", "-d", "--name", args.container,
            "-p", f"127.0.0.1:{args.port}:8080",  # loopback only
            args.tag,
        ]
    )
    container_id = inspect(args.container, "{{.Id}}")
    started = time.monotonic()
    test_proc: subprocess.CompletedProcess | None = None
    try:
        wait_for(base + "/healthz", 60)
        env = dict(os.environ, DEMO_BASE_URL=base)
        test_proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            capture_output=True, text=True, cwd=ROOT, env=env,
        )
        print(test_proc.stdout)
        print(test_proc.stderr)
    finally:
        logs = subprocess.run(["docker", "logs", args.container], capture_output=True, text=True, cwd=ROOT)
        subprocess.run(["docker", "rm", "-f", args.container], capture_output=True, cwd=ROOT)
        write(os.path.join(out, "container.log"), (logs.stdout or "") + (logs.stderr or ""))

    assert test_proc is not None
    summary = {
        "label": args.label,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "commit": git("rev-parse", "HEAD"),
        "tree_dirty": bool(git("status", "--porcelain")),
        "image_tag": args.tag,
        "image_id": image_id,
        "container_id": container_id,
        "base_url": base,
        "test_command": f"{os.path.basename(sys.executable)} -m unittest discover -s tests -v",
        "test_exit_code": test_proc.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    write(os.path.join(out, "checks.json"), json.dumps(summary, indent=2))
    write(os.path.join(out, "checks.txt"), test_proc.stdout + test_proc.stderr)
    print(f"checks against image {image_id}: {'PASS' if test_proc.returncode == 0 else 'FAIL'} (exit {test_proc.returncode})")
    return test_proc.returncode


def cmd_clean(args: argparse.Namespace) -> int:
    subprocess.run(["docker", "rm", "-f", args.container], capture_output=True, cwd=ROOT)
    subprocess.run(["docker", "image", "rm", "-f", args.tag], capture_output=True, cwd=ROOT)
    print(f"removed container {args.container} and image {args.tag} (if they existed)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--tag", default=DEFAULT_TAG)
        p.add_argument("--label", default="local")
        p.add_argument("--container", default=DEFAULT_CONTAINER)

    b = sub.add_parser("build", help="build the image and record its runtime inventory")
    common(b)
    b.add_argument("--platform", default=DEFAULT_PLATFORM)
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="run the integration checks against the built image")
    common(c)
    c.add_argument("--port", type=int, default=8080)
    c.set_defaults(func=cmd_check)

    k = sub.add_parser("clean", help="remove the container and the image")
    common(k)
    k.set_defaults(func=cmd_clean)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
