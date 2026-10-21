"""Explicit named clients; no implicit trade broadcast or shared account state."""

import asyncio


class ClientPool:
    def __init__(self, clients):
        self._clients = dict(clients)
        if not self._clients or len({id(c) for c in self._clients.values()}) != len(self._clients):
            raise ValueError("Supply distinct clients under unique account names")

    def __getitem__(self, name):
        return self._clients[name]

    def __iter__(self):
        return iter(self._clients)

    async def connect(self):
        results = await asyncio.gather(
            *(c.connect() for c in self._clients.values()), return_exceptions=True
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            await self.close()
            raise failures[0]
        return self

    async def close(self):
        results = await asyncio.gather(
            *(c.close() for c in self._clients.values()), return_exceptions=True
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            raise failures[0]

    async def balances(self):
        values = await asyncio.gather(*(c.get_balance() for c in self._clients.values()))
        return dict(zip(self._clients, values, strict=True))

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, *args):
        await self.close()
