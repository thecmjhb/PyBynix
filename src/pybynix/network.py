"""Static DNS overrides that retain the original hostname for TLS and HTTP Host."""

import importlib.util
import ipaddress
import socket
import ssl

import aiohttp
import httpcore
import httpx


def validate_dns(overrides):
    return {
        str(host).lower(): str(ipaddress.ip_address(address))
        for host, address in (overrides or {}).items()
    }


def validate_dns_servers(servers):
    values = tuple(str(ipaddress.ip_address(s)) for s in servers)
    if values and importlib.util.find_spec("dns") is None:
        raise ValueError("Custom DNS servers require pip install 'pybynix[network]'")
    return values


class DNSLookup:
    def __init__(self, overrides=None, *, nameservers=(), port=53):
        self.overrides = validate_dns(overrides)
        self.nameservers = validate_dns_servers(nameservers)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("DNS port must be 1..65535")
        self.port = port
        self._resolver = None

    async def address(self, host):
        if host.lower() in self.overrides:
            return self.overrides[host.lower()]
        if not self.nameservers:
            return host
        import dns.asyncresolver
        import dns.exception
        import dns.resolver

        if self._resolver is None:
            self._resolver = dns.asyncresolver.Resolver(configure=False)
            self._resolver.nameservers = list(self.nameservers)
            self._resolver.port = self.port
            self._resolver.cache = dns.resolver.Cache()
        try:
            answer = await self._resolver.resolve(host, "A", lifetime=5)
            return str(answer[0])
        except dns.exception.DNSException:
            raise OSError("Configured DNS server did not resolve the hostname") from None


class StaticResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, overrides=None, *, nameservers=(), port=53):
        self.overrides = validate_dns(overrides)
        self.lookup = DNSLookup(overrides, nameservers=nameservers, port=port)
        self._default = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        address = await self.lookup.address(host)
        if address == host:
            return await self._default.resolve(host, port, family)
        af = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": af,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
        ]

    async def close(self):
        await self._default.close()


class _Backend(httpcore.AnyIOBackend):
    def __init__(self, lookup):
        self.lookup = lookup

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            address = await self.lookup.address(host)
        except OSError:
            raise httpcore.ConnectError("DNS resolution failed") from None
        return await super().connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, response):
        self.response = response

    async def __aiter__(self):
        try:
            async for chunk in self.response.stream:
                yield chunk
        except httpcore.TimeoutException:
            raise httpx.TimeoutException("Response stream timeout") from None
        except (httpcore.NetworkError, httpcore.ProtocolError):
            raise httpx.TransportError("Response stream failed") from None

    async def aclose(self):
        await self.response.aclose()


class DNSHTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self, overrides=None, *, nameservers=(), port=53):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_Backend(DNSLookup(overrides, nameservers=nameservers, port=port)),
        )

    async def handle_async_request(self, request):
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self.pool.handle_async_request(core_request)
        except httpcore.TimeoutException:
            raise httpx.TimeoutException("DNS transport timeout", request=request) from None
        except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.ConnectionNotAvailable):
            raise httpx.TransportError("DNS transport failed", request=request) from None
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_ResponseStream(response),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()
