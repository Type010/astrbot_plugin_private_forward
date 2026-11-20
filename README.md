# 私聊嵌套转发

私聊对机器人发送“转发”指令开启收集，之后你发的消息会先暂存；等你发送“结束转发”后，插件才把这些消息**合并成一条嵌套合并转发**发送到指定群聊。

> 仅支持 aiocqhttp / OneBot v11 平台（SnowLuma、NapCat、LLOneBot 等）。

## 安装

把整个 `astrbot_plugin_private_forward` 目录放到 AstrBot 的插件目录：

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

开启后，你接下来发送的消息会先被收集，不会立刻发到群里。  
发送完后再发：

```text
结束转发
```

插件才会把本次收集的所有消息合并成一条嵌套合并转发发送到目标群。

“停止转发”是“结束转发”的别名，效果相同。

如果提示 `group message delivery was not confirmed`，说明 SnowLuma 收到了 QQ 的 `result=0`，但没有拿到 `groupSequence`。这**不代表消息已经发出**；默认会保留消息并报错：

- 先去目标群确认是否出现；
- 没出现可以再次发送 `结束转发` 重试；
- 只有确认目标群能收到、只是缺回执时，才把 `treat_unconfirmed_as_sent` 设为 `true`，按已发送处理并清空缓存。

也可以随时查看已收集条数：

```text
转发状态
```

## 嵌套层数

层数作用于**整批消息**，不是每条消息单独嵌套：

- `1`：普通合并转发，收集到的多条消息直接作为转发内容
- `2`：整批消息外面再套一层 node
- `3`：整批消息外面再套两层 node

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

> 插件优先读取 OneBot 原始 `message` 数组，而不是只依赖 AstrBot 解析后的组件。  
> AstrBot 4.29.x 对部分 SnowLuma 图片段解析失败时，原始段兜底可以避免整条图片消息变成 `[空消息]`。

## 隐藏发送者信息

配置 `hide_sender_info: true` 后，所有转发节点会：

- `user_id` 改成 `anonymous_user_id`；
- `nickname` 改成 `anonymous_nickname`，留空时使用零宽空格，显示为空白名称。

注意：OneBot 的合并转发节点**没有头像字段**，QQ 客户端是根据 `user_id` 去拉头像的。  
所以“默认头像”本质上是让 `user_id` 指向一个不存在/不在群里的 QQ 号，客户端拉不到头像时会显示默认灰色头像。默认填 `1000000000`，如果实际显示的不是默认头像，可以换一个不存在的 QQ 号再试。

SnowLuma 会把 `user_id <= 0` 替换成机器人自己的 QQ，所以不要填 0，否则会显示机器人头像。

另外，插件只改转发节点的元数据；如果你在消息正文或引用文本里自己打了 QQ 号/昵称，内容里仍然会显示。

## 配置

在 AstrBot 插件配置里修改：

| 配置 | 默认 | 说明 |
|---|---|---|
| `allowed_user_ids` | `[]` | 允许使用的 QQ；留空仅 AstrBot 管理员可用 |
| `allowed_group_ids` | `[]` | 允许作为目标的群；留空不限制 |
| `hide_sender_info` | `false` | 隐藏发送者 QQ 和头像，使用匿名节点 |
| `anonymous_user_id` | `1000000000` | 匿名节点使用的 QQ 号，决定默认头像 |
| `anonymous_nickname` | `""` | 匿名节点名称；留空用零宽空格 |
| `default_depth` | `1` | 默认嵌套层数 |
| `max_depth` | `3` | 最大嵌套层数，硬上限 3 |
| `session_timeout_seconds` | `600` | 会话超时，超时后丢弃已收集但未发送的消息 |
| `max_messages_per_session` | `50` | 单次会话最多收集条数，达到后需发送“结束转发”；0 为不限 |
| `include_reply_text` | `true` | 是否附带引用文本 |
| `max_reply_text_chars` | `200` | 引用文本截断长度 |
| `send_ack` | `false` | 每收集一条是否私聊回复当前条数 |
| `treat_unconfirmed_as_sent` | `false` | 只有确认群能收到、只是缺回执时才开启；默认保留消息并报错 |

## 权限与安全

- 默认只有 AstrBot 管理员能开启转发；普通用户需要加入 `allowed_user_ids`。
- 建议配置 `allowed_group_ids`，只允许转发到自己的群，避免被用于向任意群发广告。
- 私聊会话按用户隔离，A 用户开启后不会影响 B 用户。
- 收集阶段不会调用群接口；只有发送“结束转发”时才真正发到群里。
- 合并发送成功后会清除会话；未确认回执默认保留消息并报错，可重试。
- 会话超时会丢弃未发送消息，请留意 `转发状态` 里的剩余时间。

## 常见问题

**消息什么时候才会发到群里？**  
不是一条一条发。发送“结束转发”时才把本次收集的全部消息合并成一条嵌套转发发出去。

**提示“没有收集到消息”**  
开启“转发”后还没发任何消息，或会话已超时。

**提示“无法访问群”**  
机器人不在该群，或 OneBot 的 `get_group_info` 不可用。先把机器人拉进目标群。

**开启时提示“机器人不在群”**  
插件会用 `get_group_member_info` 检查机器人是否是目标群成员；检查失败会拒绝开启。确认机器人已被拉进群，并且 OneBot 的成员接口正常。

**开启时提示“无法确认你是否在目标群”**  
私聊账号没有查到目标群成员信息。转发节点会带这个 QQ；如果它不在群里，QQ 可能不确认合并转发。先把私聊账号拉进群，或开启 `hide_sender_info` 并使用 `anonymous_user_id=0`（机器人身份）。

**提示“合并转发失败：forward nesting depth exceeds 3”**  
层数超过 SnowLuma 上限，改成 1–3。

**提示“group message delivery was not confirmed”**  
SnowLuma 收到 `result=0` 但缺 `groupSequence`。这不一定代表已送达；默认保留消息并报错。先去目标群确认，没看到就重试；确认只是缺回执时才把 `treat_unconfirmed_as_sent` 设为 `true`。

**匿名后发送失败**  
`anonymous_user_id` 填了一个 QQ 不认的号时，可能拿不到发送回执。先试 `hide_sender_info=false` 确认正常发送，再换一个真实存在的、带头像要求的匿名 QQ 号，或填 `0` 让 SnowLuma 使用机器人自己的 QQ。注意填 0 会显示机器人头像。

**合并转发里出现 `[空消息]`**  
v1.4.0 起会优先读 OneBot 原始 `message` 数组兜底。如果仍然出现空消息，说明原始事件里没有可用的 message 段，或该消息类型完全没有内容；看 AstrBot 日志里是否有 `消息段解析失败`。

**图片变成 `[图片]` 占位**  
图片转换 base64 失败，或不是可访问的 http/base64 图片。看 AstrBot 日志里的 `[private_forward]` 调试信息。

**开启了收集但消息没发到群里**  
确认最后发了“结束转发”；只发消息不会自动发送。再检查目标群号、机器人是否在群、OneBot 反向 WS 是否在线。

**群里看不到嵌套效果**  
QQ NT 对很深的嵌套会折叠成“查看更多”；SnowLuma 服务端最多 3 层，超过会被拒绝。
