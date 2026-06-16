from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any

try:
    import botpy
    from botpy import logging as botpy_logging
except ImportError:  # pragma: no cover
    botpy = None  # type: ignore[assignment]
    botpy_logging = None  # type: ignore[assignment]

from .async_compat import run_blocking
from .attachments import Attachment, AttachmentStore
from .config import QQ_CHANNEL, WORKSPACE
from .message import InboundMessage, MessageBus, OutboundMessage


class BaseChannel(ABC):
    """聊天平台的抽象基类"""

    name: str = "base"

    def __init__(self, bus: MessageBus):
        self.bus = bus

    @abstractmethod
    async def start(self):
        ...

    @abstractmethod
    async def stop(self):
        ...

    @abstractmethod
    async def send(self, msg: OutboundMessage):
        ...

    async def handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        attachments: list[Attachment] | None = None,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        attachment_items = list(attachments or [])
        if not attachment_items and media:
            attachment_items = [
                Attachment(name=Path(item).name or "file", path=str(item))
                for item in media
            ]
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                attachments=attachment_items,
                metadata=metadata or {},
            )
        )


class CLIChannel(BaseChannel):
    """从终端读写"""

    name = "cli"

    def __init__(self, bus: MessageBus):
        super().__init__(bus)
        self._response_event: asyncio.Event | None = None

    async def start(self):
        loop = asyncio.get_event_loop()
        while True:
            user_input = await loop.run_in_executor(None, lambda: input("You: ").strip())
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                return
            self._response_event = asyncio.Event()
            await self.handle_message("user", "direct", user_input)
            await self._response_event.wait()

    async def stop(self):
        pass

    async def send(self, msg: OutboundMessage):
        print(f"Bot: {msg.content}\n")
        if self._response_event is not None:
            self._response_event.set()

"""
用 botpy 连接 QQ 平台，接收 QQ 私聊消息，把消息丢进 MessageBus;
 等 Agent 生成回复后，再通过 botpy.api.post_c2c_message() 发回 QQ。
"""
"""
QQ 用户发消息
    ↓
QQ 开放平台通过 WebSocket 推给 botpy
    ↓
MiniAgentQQBot.on_c2c_message_create(message)
    ↓
QQChannel._handle_c2c_message(message)
    ↓
取 message_id,去重
    ↓
取 openid
    ↓
allowFrom 白名单检查
    ↓
取 content
    ↓
可选发送 ackMessage
    ↓
_publish_inbound_to_main_loop()
    ↓
BaseChannel.handle_message()
    ↓
MessageBus.publish_inbound()
    ↓
MiniAgentApp.inbound_worker()
    ↓
Agent 生成回复
    ↓
MessageBus.publish_outbound()
    ↓
MiniAgentApp.outbound_worker()
    ↓
QQChannel.send()
    ↓
_send_private_text()
    ↓
botpy.api.post_c2c_message()
    ↓
QQ 用户收到回复
"""
class QQChannel(BaseChannel):
    """腾讯 QQ 开放平台私聊渠道，使用 botpy + WebSocket 长连接。"""

    name = "qq"

    def __init__(
        self,
        bus: MessageBus,
        config: dict[str, Any] | None = None,
        workspace: Path = WORKSPACE,
        attachment_store: AttachmentStore | None = None,
    ):
        super().__init__(bus)
        cfg = dict(QQ_CHANNEL)
        if config:
            cfg.update(config)

        self.enabled = bool(cfg.get("enabled", True))
        self.app_id = str(cfg.get("appId", "")).strip()
        self.secret = str(cfg.get("secret", "")).strip()
        # allow_from 是一个可选的白名单，指定允许接入的用户 openid 列表。如果非空，则只处理来自这些用户的消息。
        self.allow_from = {
            str(item).strip()
            for item in (cfg.get("allowFrom") or [])
            if str(item).strip()
        }
        self.msg_format = str(cfg.get("msgFormat", "plain")).strip().lower() or "plain" # 消息格式
        self.ack_message = str(cfg.get("ackMessage", "")).strip()   # 回复用户的消息已收到确认文本，留空则不回复
        self.attachment_store = attachment_store or AttachmentStore(workspace)
        """
        `_processed_ids`   保存最近处理过的消息 ID,避免重复处理               
        `_chat_type_cache` 记录 chat_id 的聊天类型，目前只用到 private       
        `_send_seq`        每个 openid 的发送序号，用于 QQ API 的 `msg_seq` 
        """
        self._processed_ids: deque[str] = deque(maxlen=1000)
        self._chat_type_cache: dict[str, str] = {}
        self._send_seq: dict[str, int] = {}
        """
        _main_loop	            MiniAgent 主程序的 asyncio 事件循环
        _bot_loop	            botpy 所在线程里的 asyncio 事件循环
        _thread	                运行 botpy的后台线程
        _client	                botpy.Client实例
        _ready	                用于通知主线程:QQ bot 已连接成功
        _stopped	            用于通知主线程:QQ bot 已停止
        _start_error	        启动时发生的异常
        _shutting_down	        是否正在关闭，用于避免退出时报错刷屏
        """
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._bot_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._start_error: Exception | None = None
        self._shutting_down = False

    async def start(self):
        if not self.enabled:
            print("[QQ] QQ channel disabled in config.")
            return

        if botpy is None:
            raise RuntimeError(
                "QQChannel requires botpy. Install it with: pip install qq-botpy"
            )
        if not self.app_id or not self.secret:
            raise RuntimeError(
                "QQChannel requires channels.qq.appId and channels.qq.secret in config.py"
            )
        if self._thread is not None: # 避免重复启动
            return
        """
        因为后面 QQ 消息是在 botpy 线程里收到的，但 Mini Agent 的 MessageBus 工作在主事件循环里。
        所以必须保存主 loop,后面通过:
        asyncio.run_coroutine_threadsafe(..., self._main_loop) 把 QQ 消息安全地投递回主程序。
        """
        self._main_loop = asyncio.get_running_loop()
        # 每次启动前清理状态。
        self._ready.clear()
        self._stopped.clear()
        self._start_error = None
        self._shutting_down = False

        # 启动 botpy 的线程会调用 self._run_bot_forever，在那里创建 botpy.Client 实例并连接 QQ。
        self._thread = threading.Thread(
            target=self._run_bot_forever,
            name="miniagent-qq-botpy",
            daemon=True,
        )
        self._thread.start()

        # 等待 botpy 启动完成
        await asyncio.wait_for(run_blocking(self._ready.wait), timeout=30)
        if self._start_error is not None:
            raise self._start_error

        print(f"[QQ] Connected via botpy WebSocket (appId: {self.app_id})")

        # 连接成功后等待停止
        await run_blocking(self._stopped.wait)

    async def stop(self):
        # 读取当前状态,拿到 botpy 所在的事件循环、botpy client、后台线程。
        loop = self._bot_loop
        client = self._client
        thread = self._thread

        print("[QQ] Stopping QQ channel...")
        self._shutting_down = True

        if loop is not None and client is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._shutdown_bot_client(client),
                    loop,
                )
                try:
                    await run_blocking(future.result, 3)
                except concurrent.futures.TimeoutError:
                    print("[QQ] Timed out while waiting for QQ bot shutdown.")
                except Exception as exc:
                    print(f"[QQ] Stop failed: {exc}")
            except Exception as exc:
                print(f"[QQ] Failed to schedule QQ shutdown: {exc}")

        if thread is not None and thread.is_alive():
            try:
                await run_blocking(thread.join, 3)
            except Exception as exc:
                print(f"[QQ] Thread join failed: {exc}")
            if thread.is_alive():
                print("[QQ] Bot thread did not exit in time; continuing shutdown.")
            else:
                print("[QQ] QQ bot thread stopped.")

        self._thread = None

    async def send(self, msg: OutboundMessage):
        """向QQ发送消息,目前仅支持私聊。
        当收到消息时,QQChannel会把它转换成 InboundMessage 发布到 MessageBus,
        供 Agent 消费并生成回复。生成的回复再通过 send() 发回 QQ。"""
        if msg.channel != self.name:
            return
        if self._client is None or self._bot_loop is None:
            raise RuntimeError("QQChannel is not connected.")

        """
        解析 chat_id  格式:private:<openid>
        """
        message_type, target_id = self._parse_chat_id(msg.chat_id)
        if message_type != "private":
            raise RuntimeError("QQChannel currently supports private chat only.")

        # 把发送任务投递到 botpy 线程
        """
        主程序 loop
            ↓ run_coroutine_threadsafe
        botpy loop
            ↓
        _send_private_text()
            ↓
        QQ 平台 API
        """
        send_coro = self._send_private_text(target_id, msg.content, msg.metadata or {})
        future = asyncio.run_coroutine_threadsafe(send_coro, self._bot_loop)
        await asyncio.wrap_future(future)

    # botpy 线程的主函数, 是整个 QQChannel 的核心。
    def _run_bot_forever(self):
        assert botpy is not None

        loop = asyncio.new_event_loop()  # 每个线程都需要自己的 asyncio event loop。
        self._bot_loop = loop
        asyncio.set_event_loop(loop)

        if botpy_logging is not None:
            botpy_logging.configure_logging(level=20)

        channel = self

        # 继承自 botpy.Client 的内部类，用来接收 QQ 平台事件
        class MiniAgentQQBot(botpy.Client):  # type: ignore[misc]

            """
            1. 保存 botpy client 到 channel._client
            2. 设置 _ready,通知主线程:QQ bot 已经启动成功
            """
            async def on_ready(self):
                channel._client = self
                channel._ready.set()

            async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
                if channel._shutting_down:
                    return
                print(f"[QQ] Event handler error in {event_method}")
                await super().on_error(event_method, *args, **kwargs)

            # 当用户给 QQ bot 发 C2C 私聊消息时，调用 _handle_c2c_message()
            async def on_c2c_message_create(self, message):
                await channel._handle_c2c_message(message)

            # 处理 direct message，然后内部也会转给 _handle_c2c_message()
            async def on_direct_message_create(self, message):
                await channel._handle_direct_message(message)

        try:
            """
            创建 botpy client 并运行,这里是真正连接 QQ 开放平台的地方。
            构造 intents ,创建 botpy client
            使用 appId/secret 登录 QQ 开放平台
            建立 WebSocket 长连接,开始接收消息事件
            """
            intents = self._build_intents()
            client = MiniAgentQQBot(intents=intents)
            self._client = client
            client.run(appid=self.app_id, secret=self.secret)
        except BaseException as exc:
            if self._shutting_down:
                pass
            else:
                # 如果启动失败，保存异常到 _start_error，并设置 _ready，这样 start() 不会一直等。
                self._start_error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                self._ready.set()
        finally:
            # 取消所有未完成任务。
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            self._stopped.set()
            self._client = None
            self._bot_loop = None
            self._thread = None
            loop.close()

    def _build_intents(self):
        """
        _build_intents()：构造 QQ 事件订阅
        intents 决定 botpy 会接收哪些类型的 QQ 事件。
        """
        assert botpy is not None

        ctor = botpy.Intents
        supported_kwargs: dict[str, Any] = {}
        valid_flags = getattr(ctor, "VALID_FLAGS", {}) or {}

        if valid_flags:
            for key in ("public_messages", "direct_message"):
                if key in valid_flags:
                    supported_kwargs[key] = True
        else:
            try:
                params = inspect.signature(ctor).parameters
            except (TypeError, ValueError):
                params = {}

            for key in ("public_messages", "direct_message"):
                if key in params:
                    supported_kwargs[key] = True

        if supported_kwargs:
            try:
                print(f"[QQ] Enabling intents: {', '.join(sorted(supported_kwargs))}")
                return ctor(**supported_kwargs)
            except TypeError:
                pass

        for kwargs in (
            {"public_messages": True, "direct_message": True},
            {"public_messages": True},
            {"direct_message": True},
            {"c2c_message": True},
            {"private_message": True},
            {},
        ):
            try:
                if kwargs:
                    print(f"[QQ] Enabling intents fallback: {kwargs}")
                return ctor(**kwargs)
            except TypeError:
                continue

        return botpy.Intents.none()

    async def _handle_c2c_message(self, message: Any):
        # 获取消息 ID 并去重
        message_id = self._message_id(message)
        if not self._remember_message_id(message_id):
            print(f"[QQ] Skip duplicate message: {message_id}")
            return

        # 获取用户 openid
        openid = self._message_author_id(message)
        if not openid:
            print(
                "[QQ] Ignore inbound message without author/openid. "
                f"type={type(message).__name__} payload={message}"
            )
            return
        # 如果 allow_from 白名单非空，且用户 openid 不在白名单里，则忽略这条消息。
        if self.allow_from and openid not in self.allow_from:
            print(f"[QQ] Ignore inbound message from non-allowed user: {openid}")
            return

        # 构造 chat_id,后面发送回复时 send() 会通过这个 chat_id 知道应该发给哪个 openid。
        chat_id = f"private:{openid}"
        self._chat_type_cache[chat_id] = "private"

        # 提取消息内容，目前仅支持纯文本消息。后续可以根据 self.msg_format 支持 markdown 或其他格式
        if self.ack_message:
            try:
                await self._send_private_text(openid, self.ack_message, {"msg_id": message_id})
            except Exception as exc:
                print(f"[QQ] Ack failed -> {openid}: {exc}")

        content = self._message_content(message)
        attachments = await self._extract_attachments(message, openid, message_id)
        if not content and not attachments:
            print(f"[QQ] Ignore empty inbound message from {openid}.")
            return

        preview = content or f"[{len(attachments)} attachment(s)]"
        print(f"[QQ] Inbound message <- {openid}: {preview}")

        """
        投递到主事件循环，后续会在 MessageBus 里被 Agent 消费。 
        因为 _handle_c2c_message() 当前运行在 botpy 线程的 event loop 里，但 Mini Agent 的 MessageBus 在主 event loop 里。
        所以不能直接 await self.handle_message(...)，要通过 _publish_inbound_to_main_loop() 投递回主 loop。
        """
        await self._publish_inbound_to_main_loop(
            sender_id=openid,
            chat_id=chat_id,
            content=content,
            attachments=attachments,
            metadata={"message_id": message_id},
        )

    async def _handle_direct_message(self, message: Any):
        author_id = self._message_author_id(message)
        if not author_id:
            return
        await self._handle_c2c_message(message)


    async def _send_private_text(self, openid: str, content: str, metadata: dict[str, Any]):
        """真正调用 QQ 开放平台 API 发消息"""
        api = getattr(self._client, "api", None)
        if api is None:
            raise RuntimeError("botpy client API is unavailable.")

        msg_id = str(metadata.get("message_id") or metadata.get("msg_id") or "").strip()
        seq = self._next_seq(openid)
        """
        | `content`  | 要发送的文本        |
        | `msg_type` | 消息类型，`0` 表示文本 |
        | `msg_seq`  | 发送序号          |
        | `msg_id`   | 可选，关联用户消息     |
        """
        payload = {
            "content": content,
            "msg_type": 0,
            "msg_seq": seq,
        }
        if msg_id:
            payload["msg_id"] = msg_id

        post_c2c = getattr(api, "post_c2c_message", None)
        if post_c2c is None:
            raise RuntimeError("Installed botpy does not expose api.post_c2c_message.")

        print(f"[QQ] Outbound message -> {openid}: {content}")
        await post_c2c(openid=openid, **payload)

    
    """
    把收到的消息投递回主事件循环，构造 InboundMessage 发布到 MessageBus
    botpy 线程收到 QQ 消息
        ↓
    调用 run_coroutine_threadsafe
        ↓
    把 self.handle_message(...) 投递到主线程的 asyncio loop
        ↓
    handle_message() 发布 InboundMessage 到 MessageBus
        ↓
    MiniAgentApp.inbound_worker() 消费消息
        ↓
    Agent 生成回复
    """
    async def _publish_inbound_to_main_loop(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        attachments: list[Attachment] | None = None,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        if self._main_loop is None:
            raise RuntimeError("QQChannel main loop is unavailable.")

        future = asyncio.run_coroutine_threadsafe(
            self.handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                attachments=attachments,
                media=media,
                metadata=metadata,
            ),
            self._main_loop,
        )
        await asyncio.wrap_future(future)
        print(f"[QQ] Inbound queued to app loop: {chat_id}")

    """关闭 botpy client,用于退出程序时关闭 botpy"""
    async def _shutdown_bot_client(self, client: Any):
        close_method = getattr(client, "close", None)
        if close_method is not None:
            result = close_method()
            if asyncio.iscoroutine(result):
                await result

        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _remember_message_id(self, message_id: str) -> bool: # 消息去重
        if not message_id:
            return True
        if message_id in self._processed_ids:
            return False
        self._processed_ids.append(message_id)
        return True

    def _next_seq(self, openid: str) -> int:  # 给每个用户生成递增的 msg_seq
        current = self._send_seq.get(openid, 0) + 1
        self._send_seq[openid] = current
        return current

    def _message_id(self, message: Any) -> str:  # 从 botpy 的 message 对象里尝试取消息 ID
        for attr in ("id", "msg_id", "message_id"):
            value = getattr(message, attr, "")
            if value:
                return str(value).strip()
        return ""

    def _message_author_id(self, message: Any) -> str:  # 这个函数负责提取用户 openid
        author = getattr(message, "author", None)
        if author is not None:
            for attr in ("id", "user_openid", "member_openid", "openid"):
                value = getattr(author, attr, "")
                if value:
                    return str(value).strip()

        for attr in ("src_guild_id",):
            value = getattr(message, attr, "")
            if value:
                return str(value).strip()

        for attr in ("author_id", "openid", "user_openid", "member_openid"):
            value = getattr(message, attr, "")
            if value:
                return str(value).strip()
        return ""

    def _message_content(self, message: Any) -> str:  # 从 botpy 的 message 对象里尝试取消息内容
        content = getattr(message, "content", "")
        text = str(content or "").strip()
        if self.msg_format == "markdown": # 目前没有做特殊处理，还是直接返回文本
            return text
        return text

    async def _extract_attachments(
        self,
        message: Any,
        openid: str,
        message_id: str,
    ) -> list[Attachment]:
        items = getattr(message, "attachments", None) or []
        results: list[Attachment] = []
        for item in items:
            url = str(getattr(item, "url", "") or "").strip()
            filename = ""
            for attr in ("filename", "file_name", "name", "title"):
                value = str(getattr(item, attr, "") or "").strip()
                if value:
                    filename = value
                    break
            filename = filename or "attachment"
            content_type = str(getattr(item, "content_type", "") or "").strip()
            if not url:
                continue
            try:
                attachment = await run_blocking(
                    lambda: self.attachment_store.download_inbound_attachment(
                        channel=self.name,
                        sender_id=openid,
                        message_id=message_id,
                        filename=filename,
                        url=url,
                        content_type=content_type,
                    )
                )
            except Exception as exc:
                print(f"[QQ] Failed to download attachment {filename}: {exc}")
                continue
            results.append(attachment)
        return results

    def _parse_chat_id(self, chat_id: str) -> tuple[str, str]:
        if ":" not in chat_id:
            return "private", chat_id
        message_type, target_id = chat_id.split(":", 1)
        message_type = message_type.strip().lower()
        if message_type != "private":
            return "private", target_id.strip()
        return message_type, target_id.strip()
