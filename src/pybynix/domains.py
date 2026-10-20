"""Read-only checks of the platform's published host candidates."""

import asyncio
import json
from importlib.resources import files

import httpx


def published_hosts():
    """Snapshot from status.bynix.app, 20 October 2026; not a health guarantee."""
    return tuple(json.loads(files("pybynix").joinpath("domains.json").read_text()))


async def check_domains(*, timeout=5, concurrency=4):
    if timeout <= 0 or not isinstance(concurrency, int) or not 1 <= concurrency <= 8:
        raise ValueError("Positive timeout and concurrency 1..8 required")
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as http:

        async def probe(host):
            async with semaphore:
                try:
                    response = await http.get(f"https://{host}/api/platform/status")
                    data = response.json() if response.is_success else {}
                    return {
                        "host": host,
                        "reachable": response.is_success,
                        "trading_enabled": data.get("trading_enabled"),
                        "maintenance_mode": data.get("maintenance_mode"),
                    }
                except (httpx.HTTPError, ValueError):
                    return {"host": host, "reachable": False}

        return await asyncio.gather(*(probe(host) for host in published_hosts()))
