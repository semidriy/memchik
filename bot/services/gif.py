import aiohttp
from dataclasses import dataclass


@dataclass
class GifResult:
    id: str
    url: str
    preview_url: str
    title: str
    width: int
    height: int


async def download_gif(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.read()
