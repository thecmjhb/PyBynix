"""Opt-in read-only live transport lab. No orders, account edits or network changes.

Run from the checkout with --session PATH --out PATH. Local test relays accept
only CONNECT traffic to the selected trusted platform hostname on TLS port 443.
"""

import argparse
import asyncio
import base64
import contextlib
import json
import socket
import time
from pathlib import Path

from pybynix import Bynix, load_session


class Relay:
    def __init__(self, host, *, socks=False, authenticate=False):
        self.host, self.socks, self.authenticate = host, socks, authenticate
        self.tunnels = {}
        self.connections = 0
        self.authenticated = 0
        self.accepting = True
        self.tasks = set()

    async def __aenter__(self):
        def connected(reader, writer):
            task = asyncio.create_task(self.handle(reader, writer))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        self.server = await asyncio.start_server(connected, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    @property
    def url(self):
        scheme = "socks5" if self.socks else "http"
        auth = "lab-user:lab-password@" if self.authenticate else ""
        return f"{scheme}://{auth}127.0.0.1:{self.port}"

    async def handle(self, reader, writer):
        remote = None
        try:
            if not self.accepting:
                return
            if self.socks:
                version, size = await reader.readexactly(2)
                methods = await reader.readexactly(size)
                wanted = 2 if self.authenticate else 0
                if version != 5 or wanted not in methods:
                    return
                writer.write(bytes([5, wanted]))
                await writer.drain()
                if self.authenticate:
                    _, size = await reader.readexactly(2)
                    user = await reader.readexactly(size)
                    size = (await reader.readexactly(1))[0]
                    password = await reader.readexactly(size)
                    if (user, password) != (b"lab-user", b"lab-password"):
                        writer.write(b"\x01\x01")
                        await writer.drain()
                        return
                    self.authenticated += 1
                    writer.write(b"\x01\x00")
                    await writer.drain()
                version, command, _, address_type = await reader.readexactly(4)
                if version != 5 or command != 1:
                    return
                if address_type == 3:
                    size = (await reader.readexactly(1))[0]
                    host = (await reader.readexactly(size)).decode()
                elif address_type == 1:
                    host = socket.inet_ntoa(await reader.readexactly(4))
                else:
                    return
                port = int.from_bytes(await reader.readexactly(2), "big")
            else:
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode().split("\r\n")
                method, target, _ = lines[0].split(" ")
                if method != "CONNECT":
                    return
                host, port = target.rsplit(":", 1)
                port = int(port)
                headers = {
                    k.lower().strip(): v.strip()
                    for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)
                }
                if self.authenticate:
                    expected = "Basic " + base64.b64encode(b"lab-user:lab-password").decode()
                    if headers.get("proxy-authorization") != expected:
                        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                        await writer.drain()
                        return
                    self.authenticated += 1
            if host != self.host or port != 443:
                return
            upstream, remote = await asyncio.open_connection(host, port)
            self.connections += 1
            identifier = self.connections
            self.tunnels[identifier] = (writer, remote)
            writer.write(
                b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00"
                if self.socks
                else b"HTTP/1.1 200 Connection Established\r\n\r\n"
            )
            await writer.drain()

            async def pipe(source, destination):
                try:
                    while chunk := await source.read(65536):
                        destination.write(chunk)
                        await destination.drain()
                finally:
                    destination.close()

            await asyncio.gather(pipe(reader, remote), pipe(upstream, writer))
        except (OSError, ValueError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            if remote:
                remote.close()
            for key, pair in tuple(self.tunnels.items()):
                if writer in pair:
                    self.tunnels.pop(key, None)

    def cut(self, *, websocket_only=False):
        targets = list(self.tunnels.items())
        if websocket_only:
            targets = targets[-1:]
        for _, writers in targets:
            for writer in writers:
                writer.close()

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        self.cut()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def run(args):
    session = load_session(args.session)
    report = {"started_at_unix": time.time(), "checks": {}}
    for socks in (False, True):
        async with (
            Relay(session.host, socks=socks, authenticate=True) as proxy,
            Bynix(session=session, host=session.host, proxy=proxy.url) as client,
        ):
            candles = await client.get_candles("EUR/USD OTC", count=2)
            async with await client.subscribe_ticks("EUR/USD OTC") as ticks:
                tick = await asyncio.wait_for(anext(ticks), 15)
            key = "authenticated_socks5" if socks else "authenticated_http_connect"
            report["checks"][key] = {
                "candles": len(candles),
                "tick": tick.symbol,
                "authenticated_tunnels": proxy.authenticated,
            }
    async with (
        Relay(session.host) as proxy,
        Bynix(
            session=session,
            host=session.host,
            proxy=proxy.url,
            timeout=3,
            read_retries=0,
            poll_interval=0.5,
        ) as client,
    ):
        async with await client.subscribe_ticks("EUR/USD OTC") as ticks:
            await asyncio.wait_for(anext(ticks), 10)
            proxy.accepting = False
            proxy.cut(websocket_only=True)
            # Keep the existing HTTP tunnel alive; new WebSocket tunnels are denied.
            kinds = client.connection_events()
            start = time.monotonic()
            polling_before = client.metrics["poll_fallbacks"]
            for _ in range(8):
                await client.get_platform_status()
                await asyncio.sleep(0.5)
            polling_ticks = client.metrics["poll_fallbacks"] - polling_before
            proxy.accepting = True
            while not client._socket.connected and time.monotonic() - start < 40:
                await asyncio.sleep(0.5)
            resumed = await asyncio.wait_for(anext(ticks), 12)
            report["checks"]["websocket_cut_rest_fallback"] = {
                "rest_tick_requests": polling_ticks,
                "websocket_reconnected": client._socket.connected,
                "resumed_symbol": resumed.symbol,
            }
            await kinds.aclose()
        proxy.accepting = False
        proxy.cut()
        await asyncio.sleep(3)
        proxy.accepting = True
        async with await client.subscribe_ticks("GBP/USD OTC") as ticks:
            await asyncio.wait_for(anext(ticks), 45)
            report["checks"]["complete_transport_interruption"] = {"recovered_tick": True}
    # A controlled local DNS answer tests both transport adapters without modifying OS DNS.
    import dns.message
    import dns.rrset

    address = (
        await asyncio.get_running_loop().getaddrinfo(session.host, 443, family=socket.AF_INET)
    )[0][4][0]
    queries = []

    class DNS(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, sender):
            request = dns.message.from_wire(data)
            response = dns.message.make_response(request)
            name = str(request.question[0].name).rstrip(".")
            queries.append(name)
            if name == session.host:
                response.answer.append(dns.rrset.from_text(name + ".", 30, "IN", "A", address))
            self.transport.sendto(response.to_wire(), sender)

    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        DNS, local_addr=("127.0.0.1", 0)
    )
    try:
        port = transport.get_extra_info("sockname")[1]
        async with Bynix(
            session=session, host=session.host, dns_servers=["127.0.0.1"], dns_port=port
        ) as client:
            await client.get_candles("EUR/USD OTC", count=2)
            async with await client.subscribe_ticks("EUR/USD OTC") as ticks:
                await asyncio.wait_for(anext(ticks), 10)
        report["checks"]["custom_dns_server"] = {"queries": len(queries), "tls_verification": True}
    finally:
        transport.close()
    report["finished_at_unix"] = time.time()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(parser.parse_args()))
