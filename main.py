from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.star.filter.event_message_type import EventMessageType


COMMAND_WORDS = {"转发", "结束转发", "停止转发", "转发状态"}
MAX_HARD_DEPTH = 3


@dataclass
class ForwardSession:
    group_id: str
    depth: int
    expires_at: float
    last_sent_at: float = 0.0
    count: int = 0


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

    async def _build_nodes(
        self, event: AstrMessageEvent, depth: int
    ) -> list[dict]:
        segments = await self._to_segments(event)
        sender_id = str(event.get_sender_id())
        user_id: int | str
        try:
            user_id = int(sender_id)
        except ValueError:
            user_id = sender_id
        nickname = event.get_sender_name() or sender_id

        node: dict = {
            "type": "node",
            "data": {
                "user_id": user_id,
                "nickname": nickname,
                "content": segments,
            },
        }

        # depth=1：单层合并转发；depth=2/3：再套 1/2 层 node。
        for _ in range(max(0, depth - 1)):
            node = {
                "type": "node",
                "data": {
                    "user_id": user_id,
                    "nickname": nickname,
                    "content": [node],
                },
            }

        return [node]

    # ---------------- 会话展示 ----------------

    @staticmethod
    def _status_text(session: ForwardSession) -> str:
        remain = max(0, int(session.expires_at - time.time()))
        return (
            f"目标群：{session.group_id}\n"
            f"嵌套层数：{session.depth}\n"
            f"已转发：{session.count} 条\n"
            f"剩余时间：{remain} 秒"
        )

    @staticmethod
    def _usage() -> str:
        return (
            "用法：\n"
            "转发 <群号> [嵌套层数 1-3]\n"
            "结束转发 / 停止转发\n"
            "转发状态"
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
        self.sessions[str(event.get_sender_id())] = ForwardSession(
            group_id=group_id,
            depth=selected_depth,
            expires_at=now + timeout,
        )

        note = ""
        if depth and depth > max_depth:
            note = f"\n（请求层数 {depth} 超过上限，已改为 {max_depth} 层）"

        yield event.plain_result(
            f"已开启转发：\n"
            f"目标群：{group_id}\n"
            f"嵌套层数：{selected_depth}\n"
            f"发送“结束转发”停止。{note}"
        )
        event.stop_event()

    @filter.command("结束转发", alias={"停止转发"})
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def stop_forward(self, event: AstrMessageEvent):
        """结束转发会话"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用“结束转发”。")
            event.stop_event()
            return

        removed = self.sessions.pop(str(event.get_sender_id()), None)
        if removed:
            yield event.plain_result(
                f"已结束转发，本次共转发 {removed.count} 条消息。"
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
            self.sessions.pop(str(event.get_sender_id()), None)
            yield event.plain_result("转发会话已超时，请重新开启。")
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
            self.sessions.pop(user_id, None)
            yield event.plain_result(
                "转发会话已超时，请重新发送“转发 <群号> [层数]”。"
            )
            event.stop_event()
            return

        max_messages = self._get_int("max_messages_per_session", 50)
        if max_messages > 0 and session.count >= max_messages:
            self.sessions.pop(user_id, None)
            yield event.plain_result(
                f"本次转发已达到 {max_messages} 条上限，会话已结束。"
            )
            event.stop_event()
            return

        min_interval = max(0, self._get_int("min_interval_seconds", 2))
        if min_interval and now - session.last_sent_at < min_interval:
            event.stop_event()
            return

        try:
            nodes = await self._build_nodes(event, session.depth)
            await self._call_action(
                event,
                "send_group_forward_msg",
                group_id=int(session.group_id),
                messages=nodes,
            )
        except Exception as exc:
            logger.error(f"[private_forward] 转发到群 {session.group_id} 失败: {exc}")
            yield event.plain_result(f"转发失败：{exc}")
            event.stop_event()
            return

        session.last_sent_at = now
        session.count += 1
        session.expires_at = now + max(
            30, self._get_int("session_timeout_seconds", 600)
        )

        if self._get_bool("send_ack", False):
            yield event.plain_result("已转发")
        event.stop_event()

    async def terminate(self):
        self.sessions.clear()
