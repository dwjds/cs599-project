from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .attachments import Attachment


@dataclass
class InboundMessage:
    """从外部收到的消息"""

    channel: str
    sender_id: str
    chat_id: str
    content: str
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"

    @property
    def media(self) -> list[str]:
        return [item.path for item in self.attachments]


@dataclass
class OutboundMessage:
    """要发送到外部的消息"""

    channel: str
    chat_id: str
    content: str
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def media(self) -> list[str]:
        return [item.path for item in self.attachments]


class MessageBus:
    """消息总线，负责在不同渠道之间传递消息"""

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage):
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage):
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()
