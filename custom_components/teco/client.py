"""Thin async client for the TECO sidecar service."""
from __future__ import annotations

import asyncio

import aiohttp


class TecoSidecarError(Exception):
    """Sidecar unreachable or returned an error."""


class TecoClient:
    """Talks to the sidecar's HTTP API (no browser logic here)."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str | None = None):
        self._session = session
        self._base = url.rstrip("/")
        self._headers = {"X-Auth-Token": token} if token else {}

    async def health(self) -> dict:
        return await self._get("/health", timeout=15)

    async def get_data(self, force: bool = False) -> dict:
        # first run backfills many bills -> allow a long timeout
        path = "/data" + ("?force=true" if force else "")
        return await self._get(path, timeout=600)

    async def export(self) -> dict:
        return await self._get("/export", timeout=60)

    async def _get(self, path: str, timeout: int) -> dict:
        try:
            async with self._session.get(
                self._base + path, headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise TecoSidecarError(f"{path} -> HTTP {resp.status}: {text[:200]}")
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise TecoSidecarError(f"{path} -> {e}") from e
