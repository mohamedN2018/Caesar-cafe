#!/usr/bin/env python3
"""
Every published port binds to loopback.

This is the single property that lets one compose file serve both local
development and a public VPS, so it gets a guard rather than a comment.

Written as `127.0.0.1:8010:8000`, the mapping is a convenience locally and a
no-op in production — the internet cannot reach it, and Traefik talks to the web
container over the proxy network instead. Written as `8010:8000`, the same line
publishes an unencrypted Django to the world, beside the TLS every user assumes
they are getting.

The reason this is checked by a machine: **the bad version works perfectly.**
Locally, `8010:8000` and `127.0.0.1:8010:8000` are indistinguishable — same curl,
same browser, same tests. Nothing fails until the file is on a public host, and
then nothing fails visibly at all. A human reviewer reads the diff and sees a
port mapping, which is what they expected to see.

Usage:
    python scripts/check_compose_ports.py            # reads `docker compose config`
    python scripts/check_compose_ports.py <file.json> # or a saved config
"""

from __future__ import annotations

import json
import subprocess
import sys

ALLOWED_HOSTS = {"127.0.0.1", "::1"}


def load(argv: list[str]) -> dict:
    if len(argv) > 1:
        return json.loads(open(argv[1], encoding="utf-8").read())

    # `--profile dev` so the dev-only services are included. A guard that skips
    # the services it was not looking at is not a guard.
    result = subprocess.run(
        ["docker", "compose", "--profile", "dev", "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("docker compose config failed — fix that before this check")
    return json.loads(result.stdout)


def main(argv: list[str]) -> int:
    config = load(argv)
    offenders: list[str] = []
    checked = 0

    for name, service in sorted(config.get("services", {}).items()):
        for port in service.get("ports", []):
            checked += 1
            # An absent `host_ip` means every interface, which is the failure this
            # exists to catch — it must never be read as "unset, so probably fine".
            host_ip = port.get("host_ip") or "0.0.0.0"
            published = port.get("published", "?")
            target = port.get("target", "?")
            where = f"{name}: {host_ip}:{published} -> {target}"
            if host_ip in ALLOWED_HOSTS:
                print(f"  ok    {where}")
            else:
                print(f"  WRONG {where}")
                offenders.append(where)

    if offenders:
        print(
            "\nRefusing: a port is published beyond loopback.\n"
            "On a public host this exposes the service directly, bypassing TLS.\n"
            "Write it as 127.0.0.1:HOST:CONTAINER — see the note on api.ports.\n"
            + "\n".join(f"  - {o}" for o in offenders),
            file=sys.stderr,
        )
        return 1

    print(f"\n{checked} published port(s), all on loopback.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
