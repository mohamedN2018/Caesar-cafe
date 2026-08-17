"""
The name Caddy dials must be a name the api actually answers to.

**Why this exists.** Every `/api/*` request on the deployed site returned 502 with

    dial tcp 10.0.1.121:8000: connect: connection refused

while the api container was healthy and answering its own probe on
127.0.0.1:8000 every thirty seconds. Nothing was broken in the application, in
the migrations, or in the boot — which is why it took three rounds of reading the
wrong logs to find.

`10.0.1.0/24` is Dokploy's shared Traefik network; this project's own networks are
172.19/172.21 bridges, so that address was not this api on either of them. `web`
is attached to the shared network so Traefik can reach it, and that also means
Docker's embedded DNS answers `api` from a network where **every** project's
containers live together. Caddy asked for `api` and was handed a neighbour.

So the upstream carries a distinctive alias now. This guard is the cheap part: two
files in different languages have to agree on one string, and if they ever stop
agreeing the symptom is a total API outage that looks like an application bug.

Read across the tree the same way `test_admin_surface.py` reads the router — a
Python assertion about a Caddyfile is still cheaper than the alternative, which is
nobody checking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
CADDYFILE = ROOT / "deploy" / "Caddyfile"

if not COMPOSE.exists() or not CADDYFILE.exists():  # pragma: no cover
    pytest.skip(
        "The deploy files are not reachable from here, so this guard cannot run.\n"
        "\n"
        "That is the dev container: docker-compose.yml mounts only `./backend:/app`,\n"
        "so there is no sibling `docker-compose.yml` or `deploy/` to read. CI takes\n"
        "the whole repo, and so does a run from a checkout.\n"
        "\n"
        "A skip rather than a pass: a guard that quietly reported success on a file\n"
        "it could not open would be worse than one that says it did not look.",
        allow_module_level=True,
    )

#: `reverse_proxy caesar-cafe-api:8000 {`
UPSTREAM = re.compile(r"reverse_proxy\s+([A-Za-z0-9._-]+):(\d+)")


def caddy_upstreams() -> list[tuple[str, str]]:
    return UPSTREAM.findall(CADDYFILE.read_text(encoding="utf-8"))


def api_service() -> dict:
    # `yaml.safe_load` chokes on the `x-django` merge keys this file uses for the
    # shared Django config, so the anchors are resolved by PyYAML itself — merge
    # keys are standard YAML and `safe_load` handles `<<:` fine.
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return doc["services"]["api"]


def api_names() -> set[str]:
    """Every name the api container answers to, from the compose file."""
    service = api_service()
    names = {"api"}  # the service name is always an alias on its own networks

    networks = service.get("networks")
    if isinstance(networks, dict):
        for config in networks.values():
            if isinstance(config, dict):
                names.update(config.get("aliases") or [])
    return names


class TestTheUpstreamResolves:
    def test_the_caddyfile_has_upstreams_at_all(self) -> None:
        # Guard the guard: a rewritten Caddyfile that this regex no longer matches
        # would make every assertion below vacuous rather than failing.
        assert len(caddy_upstreams()) >= 2, (
            "expected at least the /api/* and /ws/* upstreams in the Caddyfile"
        )

    def test_every_upstream_is_a_name_the_api_answers_to(self) -> None:
        declared = api_names()
        wrong = [f"{host}:{port}" for host, port in caddy_upstreams() if host not in declared]

        assert wrong == [], (
            f"Caddy proxies to {wrong}, which the api service does not answer to. "
            f"It answers to {sorted(declared)}. A name Caddy cannot resolve to THIS "
            "api is a total /api/* outage that looks like an application bug."
        )

    def test_every_upstream_uses_the_port_gunicorn_binds(self) -> None:
        ports = {port for _host, port in caddy_upstreams()}

        assert ports == {"8000"}, f"gunicorn binds 8000; the Caddyfile dials {sorted(ports)}"

    def test_the_upstream_name_is_not_the_bare_service_name(self) -> None:
        """
        The heart of it.

        `api` is not wrong in isolation — it is wrong on a network shared with
        every other project on the host, which `web` must join for Traefik to
        reach it. A distinctive alias is the only thing that makes the name
        unambiguous, so this asserts the generic name is not what ships.
        """
        generic = [f"{host}:{port}" for host, port in caddy_upstreams() if host == "api"]

        assert generic == [], (
            "The Caddyfile dials the bare `api`. On Dokploy's shared Traefik "
            "network that name resolves to whichever project claimed it — it "
            "resolved to 10.0.1.121 and every /api/* request 502ed. Use the "
            "distinctive alias declared on the api's `edge` network."
        )

    def test_the_alias_is_declared_on_a_network_web_shares(self) -> None:
        """
        An alias on a network `web` is not attached to is a name Caddy still
        cannot resolve — the same outage with a different cause.
        """
        doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        web_networks = doc["services"]["web"].get("networks") or []
        web_names = set(web_networks if isinstance(web_networks, list) else web_networks.keys())

        api_networks = api_service().get("networks") or {}
        assert isinstance(api_networks, dict), (
            "the api's networks must be a mapping to hold aliases"
        )

        aliased_on = {
            name
            for name, config in api_networks.items()
            if isinstance(config, dict) and config.get("aliases")
        }

        assert aliased_on & web_names, (
            f"the api's alias lives on {sorted(aliased_on)}, and web is on "
            f"{sorted(web_names)} — Caddy cannot resolve a name on a network it is "
            "not attached to"
        )
