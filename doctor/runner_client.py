import httpx
from .config import get_settings


async def runner_call(operation: str, payload: dict | None = None, timeout: float = 30):
    settings = get_settings()
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=settings.runner_socket), timeout=timeout) as client:
        response = await client.post('http://runner/' + operation, json=payload or {}, headers={'X-Runner-Token': settings.runner_token})
        response.raise_for_status()
        return response.json()
