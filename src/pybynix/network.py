"""Static DNS overrides that retain the original hostname for TLS and HTTP Host."""

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


class StaticResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, overrides):
        self.overrides = validate_dns(overrides)
        self._default = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        address = self.overrides.get(host.lower())
        if address is None:
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
    def __init__(self, overrides):
        self.overrides = overrides

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        address = self.overrides.get(host.lower(), host)
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
    def __init__(self, overrides):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_Backend(validate_dns(overrides)),
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
