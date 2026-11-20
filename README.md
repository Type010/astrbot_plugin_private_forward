# 私聊嵌套转发

私聊对机器人发送“转发”指令开启会话，之后你发的消息会以**嵌套合并转发**的形式发送到指定群聊。

> 仅支持 aiocqhttp / OneBot v11 平台（SnowLuma、NapCat、LLOneBot 等）。

## 安装

把整个 `private_forward` 目录放到 AstrBot 的插件目录：

```text
/AstrBot/data/plugins/astrbot_plugin_private_forward/
```

或者在 AstrBot WebUI 里通过仓库地址/上传 zip 安装，然后重载插件。

依赖：无额外 Python 包。

## 使用

私聊机器人：

```text
转发 123456789 3
```

- `123456789`：目标群号
- `3`：嵌套层数，可省略，默认取插件配置的 `default_depth`

开启后，你接下来发送的每条消息都会以合并转发发到该群。

结束：

```text
结束转发
```

或：

```text
停止转发
```

查看状态：

```text
转发状态
```

## 嵌套层数

- `1`：普通合并转发，一层 node
- `2`：外层 node 里再套一层 node
- `3`：再套两层 node

SnowLuma 的 `MAX_FORWARD_DEPTH = 3`，超过 3 层会报：

```text
forward nesting depth exceeds 3
```

插件会把层数限制在 1–3，配置里填更大也会被压到 3。

## 支持的消息内容

- 文本
- 图片（自动转 `base64://`，不依赖 AstrBot 与 OneBot 在同一文件系统）
- @
- 引用消息（把被引用文本作为 `[引用] ...` 放进转发内容）
- 表情/语音/视频/文件：以 `[表情]`、`[语音]`、`[视频]`、`[文件]` 占位

## 配置

在 AstrBot 插件配置里修改：

| 配置 | 默认 | 说明 |
|---|---|---|
| `allowed_user_ids` | `[]` | 允许使用的 QQ；留空仅 AstrBot 管理员可用 |
| `allowed_group_ids` | `[]` | 允许作为目标的群；留空不限制 |
| `default_depth` | `1` | 默认嵌套层数 |
| `max_depth` | `3` | 最大嵌套层数，硬上限 3 |
| `session_timeout_seconds` | `600` | 会话超时，超时需重新开启 |
| `min_interval_seconds` | `2` | 同一用户两条转发的最小间隔 |
| `max_messages_per_session` | `50` | 单次会话最多转发条数，0 为不限制 |
| `include_reply_text` | `true` | 是否附带引用文本 |
| `max_reply_text_chars` | `200` | 引用文本截断长度 |
| `send_ack` | `false` | 每条成功后是否私聊回复“已转发” |

## 权限与安全

- 默认只有 AstrBot 管理员能开启转发；普通用户需要加入 `allowed_user_ids`。
- 建议配置 `allowed_group_ids`，只允许转发到自己的群，避免被用于向任意群发广告。
- 私聊会话按用户隔离，A 用户开启后不会影响 B 用户。
- 转发成功后会 `stop_event()`，不会触发 LLM 额外回复。
- 发送“结束转发”后立即清除会话。

## 常见问题

**提示“无法访问群”**  
机器人不在该群，或 OneBot 的 `get_group_info` 不可用。先把机器人拉进目标群。

**提示“转发失败：forward nesting depth exceeds 3”**  
层数超过 SnowLuma 上限，改成 1–3。

**图片变成 `[图片]` 占位**  
图片转换 base64 失败，或不是可访问的 http/base64 图片。看 AstrBot 日志里的 `[private_forward]` 调试信息。

**开启了转发但消息没发到群里**  
检查目标群号、机器人是否在群、OneBot 反向 WS 是否在线，以及 `send_group_forward_msg` 是否被平台支持。

**群里看不到嵌套效果**  
QQ NT 对很深的嵌套会折叠成“查看更多”；SnowLuma 服务端最多 3 层，超过会被拒绝。
