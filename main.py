from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.star.filter.event_message_type import EventMessageType


COMMAND_WORDS = {"转发", "结束转发", "停止转发", "取消转发", "丢弃转发", "转发状态"}
MAX_HARD_DEPTH = 3


@dataclass
class ForwardSession:
    group_id: str
    depth: int
    expires_at: float
    owner_id: str
    owner_name: str
    messages: list[dict] = field(default_factory=list)
    limit_notified: bool = False


class PrivateForwardPlugin(Star):
    """私聊嵌套转发：私聊开启会话后，把后续消息以合并转发发到指定群。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.sessions: dict[str, ForwardSession] = {}

    # ---------------- 配置读取 ----------------

    def _get_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "开"}

    def _get_id_list(self, key: str) -> set[str]:
        raw = self.config.get(key, [])
        if not isinstance(raw, list):
            return set()
        return {str(item).strip() for item in raw if str(item).strip()}

    def _is_allowed_user(self, event: AstrMessageEvent) -> bool:
        allowed = self._get_id_list("allowed_user_ids")
        if not allowed:
            return event.is_admin()
        return event.is_admin() or str(event.get_sender_id()) in allowed

    def _is_allowed_group(self, group_id: str) -> bool:
        allowed = self._get_id_list("allowed_group_ids")
        return not allowed or group_id in allowed

    def _max_depth(self) -> int:
        return max(1, min(self._get_int("max_depth", MAX_HARD_DEPTH), MAX_HARD_DEPTH))

    def _default_depth(self) -> int:
        return max(1, min(self._get_int("default_depth", 1), self._max_depth()))

    def _hide_sender_info(self) -> bool:
        return self._get_bool("hide_sender_info", False)

    def _anonymous_user_id(self) -> int:
        value = self._get_int("anonymous_user_id", 1_000_000_000)
        # SnowLuma 会把 <=0 的 userUin 替换成机器人 QQ；这里强制用正数。
        return value if value > 0 else 1_000_000_000

    def _anonymous_nickname(self) -> str:
        value = str(self.config.get("anonymous_nickname", "") or "")
        # 纯空格会被 SnowLuma 的 trim() 去掉并回退成 QQ 号；
        # 零宽空格不会被 trim，渲染出来是空白名称。
        return value if value.strip() else "\u200b"

    # ---------------- OneBot 调用 ----------------

    async def _call_action(
        self, event: AstrMessageEvent, action: str, **params: Any
    ) -> Any:
        bot = getattr(event, "bot", None)
        call = getattr(bot, "call_action", None)
        if call is None:
            raise RuntimeError("当前事件没有 OneBot call_action")

        result = await call(action, **params)
        if isinstance(result, dict) and "status" in result:
            if str(result.get("status") or "") == "failed":
                detail = (
                    result.get("wording")
                    or result.get("message")
                    or result.get("msg")
                    or result
                )
                raise RuntimeError(f"{action} 调用失败: {detail}")
            if "data" in result:
                result = result.get("data")
        return result

    # ---------------- 消息转换 ----------------

    async def _image_ref(self, image: Image) -> str | None:
        try:
            encoded = await image.convert_to_base64()
            if isinstance(encoded, bytes):
                encoded = encoded.decode("ascii", "ignore")
            if isinstance(encoded, str) and encoded:
                if encoded.startswith("data:"):
                    return encoded
                if encoded.startswith("base64://"):
                    return encoded
                return f"base64://{encoded}"
        except Exception as exc:
            logger.debug(f"[private_forward] 图片转 base64 失败: {exc}")

        for attr in ("url", "file"):
            value = getattr(image, attr, "") or ""
            if isinstance(value, str) and value.startswith(
                ("http://", "https://", "base64://", "data:")
            ):
                return value
        return None

    async def _to_segments(self, event: AstrMessageEvent) -> list[dict]:
        segments: list[dict] = []
        include_reply = self._get_bool("include_reply_text", True)
        max_reply_chars = self._get_int("max_reply_text_chars", 200)

        for comp in event.get_messages():
            if isinstance(comp, Plain):
                if comp.text:
                    segments.append(
                        {"type": "text", "data": {"text": comp.text}}
                    )
            elif isinstance(comp, Image):
                ref = await self._image_ref(comp)
                if ref:
                    segments.append({"type": "image", "data": {"file": ref}})
                else:
                    segments.append({"type": "text", "data": {"text": "[图片]"}})
            elif isinstance(comp, At):
                qq = str(getattr(comp, "qq", "") or "")
                if qq == "all":
                    segments.append(
                        {"type": "text", "data": {"text": "@全体成员"}}
                    )
                elif qq:
                    segments.append({"type": "at", "data": {"qq": qq}})
            elif isinstance(comp, Reply):
                text = str(getattr(comp, "message_str", "") or "").strip()
                if include_reply and text:
                    if max_reply_chars > 0 and len(text) > max_reply_chars:
                        text = text[:max_reply_chars] + "…"
                    segments.append(
                        {"type": "text", "data": {"text": f"[引用] {text}"}}
                    )
            else:
                placeholder = {
                    "Face": "[表情]",
                    "Record": "[语音]",
                    "Video": "[视频]",
                    "File": "[文件]",
                    "Forward": "[合并转发]",
                    "Node": "[合并转发]",
                }.get(type(comp).__name__)
                if placeholder:
                    segments.append(
                        {"type": "text", "data": {"text": placeholder}}
                    )

        return segments or [{"type": "text", "data": {"text": "[空消息]"}}]

    async def _build_message_node(self, event: AstrMessageEvent) -> dict:
        """把一条私聊消息转换成合并转发里的一个 node。"""
        segments = await self._to_segments(event)

        if self._hide_sender_info():
            user_id: int | str = self._anonymous_user_id()
            nickname = self._anonymous_nickname()
        else:
            sender_id = str(event.get_sender_id())
            try:
                user_id = int(sender_id)
            except ValueError:
                user_id = sender_id
            nickname = event.get_sender_name() or sender_id

        data: dict[str, Any] = {
            "user_id": user_id,
            "nickname": nickname,
            "content": segments,
        }
        timestamp = getattr(getattr(event, "message_obj", None), "timestamp", None)
        if isinstance(timestamp, int) and 0 < timestamp < 2**32:
            data["time"] = timestamp

        return {"type": "node", "data": data}

    def _build_merged_messages(self, session: ForwardSession) -> list[dict]:
        """把会话里收集的所有消息合并成一个（可嵌套的）合并转发。

        depth=1：所有消息 node 直接作为合并转发的 messages。
        depth=2/3：用一个/两个外层 node 把整批消息包起来。
        """
        nodes = list(session.messages)
        if session.depth <= 1:
            return nodes

        if self._hide_sender_info():
            owner_uid: int | str = self._anonymous_user_id()
            owner_name = self._anonymous_nickname()
        else:
            try:
                owner_uid = int(session.owner_id)
            except ValueError:
                owner_uid = session.owner_id
            owner_name = session.owner_name

        wrapper: dict = {
            "type": "node",
            "data": {
                "user_id": owner_uid,
                "nickname": owner_name,
                "content": nodes,
            },
        }
        for _ in range(session.depth - 2):
            wrapper = {
                "type": "node",
                "data": {
                    "user_id": owner_uid,
                    "nickname": owner_name,
                    "content": [wrapper],
                },
            }
        return [wrapper]

    # ---------------- 会话展示 ----------------

    @staticmethod
    def _status_text(session: ForwardSession) -> str:
        remain = max(0, int(session.expires_at - time.time()))
        return (
            f"目标群：{session.group_id}\n"
            f"嵌套层数：{session.depth}\n"
            f"已收集：{len(session.messages)} 条\n"
            f"剩余时间：{remain} 秒\n"
            f"发送“结束转发”后才会合并发送。"
        )

    @staticmethod
    def _usage() -> str:
        return (
            "用法：\n"
            "转发 <群号> [嵌套层数 1-3]   开始收集消息\n"
            "结束转发 / 停止转发           把已收集消息合并发送到目标群\n"
            "取消转发 / 丢弃转发           丢弃已收集消息，不发送\n"
            "转发状态                     查看已收集条数"
        )

    # ---------------- 指令 ----------------

    @filter.command("转发")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def start_forward(self, event: AstrMessageEvent):
        """转发 <群号> [嵌套层数]"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用“转发”指令。")
            event.stop_event()
            return

        if not self._is_allowed_user(event):
            yield event.plain_result("你没有使用转发功能的权限。")
            event.stop_event()
            return

        parts = (event.message_str or "").strip().split(maxsplit=1)
        raw = parts[1].strip() if len(parts) > 1 else ""
        args = raw.split()
        group_id = args[0] if args else ""
        depth = 0
        if len(args) > 1:
            try:
                depth = int(args[1])
            except ValueError:
                depth = 0
        if not group_id:
            session = self.sessions.get(str(event.get_sender_id()))
            if session:
                yield event.plain_result(
                    f"当前转发会话：\n{self._status_text(session)}"
                )
            else:
                yield event.plain_result(self._usage())
            event.stop_event()
            return

        if not group_id.isdigit():
            yield event.plain_result("群号必须是纯数字。\n" + self._usage())
            event.stop_event()
            return

        owner_id = str(event.get_sender_id())
        existing = self.sessions.get(owner_id)
        if existing and existing.messages:
            yield event.plain_result(
                f"已有未发送的转发会话（{len(existing.messages)} 条），"
                "请先发送“结束转发”。"
            )
            event.stop_event()
            return

        if not self._is_allowed_group(group_id):
            yield event.plain_result(f"不允许转发到群 {group_id}。")
            event.stop_event()
            return

        max_depth = self._max_depth()
        requested = depth if depth and depth > 0 else self._default_depth()
        selected_depth = max(1, min(int(requested), max_depth))

        try:
            info = await self._call_action(
                event, "get_group_info", group_id=int(group_id)
            )
            if not isinstance(info, dict):
                raise RuntimeError("get_group_info 返回异常")
        except Exception as exc:
            yield event.plain_result(f"无法访问群 {group_id}：{exc}")
            event.stop_event()
            return

        now = time.time()
        timeout = max(30, self._get_int("session_timeout_seconds", 600))
        self.sessions[owner_id] = ForwardSession(
            group_id=group_id,
            depth=selected_depth,
            expires_at=now + timeout,
            owner_id=owner_id,
            owner_name=event.get_sender_name() or owner_id,
        )

        note = ""
        if depth and depth > max_depth:
            note = f"\n（请求层数 {depth} 超过上限，已改为 {max_depth} 层）"

        yield event.plain_result(
            f"已开始收集消息：\n"
            f"目标群：{group_id}\n"
            f"嵌套层数：{selected_depth}\n"
            f"发送完请发“结束转发”，届时会把本次收集的消息合并成一条转发发送。{note}"
        )
        event.stop_event()

    @filter.command("结束转发", alias={"停止转发"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def stop_forward(self, event: AstrMessageEvent):
        """结束收集并合并发送"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用“结束转发”。")
            event.stop_event()
            return

        user_id = str(event.get_sender_id())
        session = self.sessions.get(user_id)
        if not session:
            yield event.plain_result("当前没有进行中的转发会话。")
            event.stop_event()
            return

        if time.time() > session.expires_at:
            count = len(session.messages)
            self.sessions.pop(user_id, None)
            yield event.plain_result(
                f"转发会话已超时，{count} 条消息未发送。"
            )
            event.stop_event()
            return

        if not session.messages:
            self.sessions.pop(user_id, None)
            yield event.plain_result("没有收集到消息，会话已结束。")
            event.stop_event()
            return

        count = len(session.messages)
        try:
            await self._call_action(
                event,
                "send_group_forward_msg",
                group_id=int(session.group_id),
                messages=self._build_merged_messages(session),
            )
        except Exception as exc:
            session.expires_at = time.time() + max(
                30, self._get_int("session_timeout_seconds", 600)
            )
            logger.error(
                f"[private_forward] 合并转发到群 {session.group_id} 失败: {exc}"
            )
            yield event.plain_result(
                f"合并转发失败：{exc}\n"
                "QQ 可能已经收到这条转发，请先去目标群确认。\n"
                "如果群里已经出现，请发送“取消转发”丢弃缓存，不要重复发送；"
                "如果没出现，可再次发送“结束转发”重试。"
            )
            event.stop_event()
            return

        self.sessions.pop(user_id, None)
        yield event.plain_result(
            f"已合并转发 {count} 条消息到群 {session.group_id}"
            f"（嵌套 {session.depth} 层）。"
        )
        event.stop_event()

    @filter.command("取消转发", alias={"丢弃转发"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def cancel_forward(self, event: AstrMessageEvent):
        """丢弃已收集消息，不发送"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用“取消转发”。")
            event.stop_event()
            return

        session = self.sessions.pop(str(event.get_sender_id()), None)
        if session:
            yield event.plain_result(
                f"已取消转发，丢弃 {len(session.messages)} 条未发送消息。"
            )
        else:
            yield event.plain_result("当前没有进行中的转发会话。")
        event.stop_event()

    @filter.command("转发状态")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def forward_status(self, event: AstrMessageEvent):
        """查看当前转发会话"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用“转发状态”。")
            event.stop_event()
            return

        session = self.sessions.get(str(event.get_sender_id()))
        if not session:
            yield event.plain_result("当前没有进行中的转发会话。")
        elif time.time() > session.expires_at:
            count = len(session.messages)
            self.sessions.pop(str(event.get_sender_id()), None)
            yield event.plain_result(
                f"转发会话已超时，{count} 条消息未发送。"
            )
        else:
            yield event.plain_result(self._status_text(session))
        event.stop_event()

    # ---------------- 私聊消息监听 ----------------

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def forward_private_message(self, event: AstrMessageEvent):
        if not event.is_private_chat():
            return

        user_id = str(event.get_sender_id())
        session = self.sessions.get(user_id)
        if not session:
            return

        text = (event.message_str or "").strip()
        first = text.split(maxsplit=1)[0] if text else ""
        if first in COMMAND_WORDS:
            return

        now = time.time()
        if now > session.expires_at:
            count = len(session.messages)
            self.sessions.pop(user_id, None)
            yield event.plain_result(
                f"转发会话已超时，{count} 条消息未发送。"
            )
            event.stop_event()
            return

        max_messages = self._get_int("max_messages_per_session", 50)
        if max_messages > 0 and len(session.messages) >= max_messages:
            if not session.limit_notified:
                session.limit_notified = True
                yield event.plain_result(
                    f"已达到 {max_messages} 条上限，请发送“结束转发”合并发送。"
                )
            event.stop_event()
            return

        try:
            session.messages.append(await self._build_message_node(event))
        except Exception as exc:
            logger.error(f"[private_forward] 收集消息失败: {exc}")
            yield event.plain_result(f"收集消息失败：{exc}")
            event.stop_event()
            return

        session.expires_at = now + max(
            30, self._get_int("session_timeout_seconds", 600)
        )

        if self._get_bool("send_ack", False):
            yield event.plain_result(f"已收集第 {len(session.messages)} 条。")
        event.stop_event()

    async def terminate(self):
        self.sessions.clear()
