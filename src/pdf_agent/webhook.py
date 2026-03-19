"""Webhook notification support — fire-and-forget HTTP callbacks."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def fire_webhook(url: str, payload: dict[str, Any], timeout: float = 10.0) -> None:
    """Send an HTTP POST to the webhook URL with the given payload.
    Errors are logged but never raised — fire-and-forget.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("Webhook delivered to %s (status=%d)", url, resp.status_code)
    except Exception as exc:
        logger.warning("Webhook delivery failed (%s): %s", url, exc)


def schedule_webhook(url: str | None, payload: dict[str, Any]) -> None:
    """Schedule a webhook call on the running event loop if url is set."""
    if not url:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(fire_webhook(url, payload))
        else:
            asyncio.run(fire_webhook(url, payload))
    except Exception as exc:
        logger.warning("Could not schedule webhook: %s", exc)
