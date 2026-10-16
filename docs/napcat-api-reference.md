# NapCat API 接口索引

> 本文档根据 NapCat 的 Apifox 官方文档自动检索整理。接口可能随 NapCat 更新而变化，调用前请以链接中的官方原文为准。

- 原始入口：[https://napcat.apifox.cn/llms.txt](https://napcat.apifox.cn/llms.txt)
- 官方首页：[NapCat 接口文档](https://napcat.apifox.cn/5430207m0)
- 抓取时间：2026-09-14 19:46:36 +08:00（Asia/Shanghai）
- 收录范围：176 个接口，39 个数据模型

## 快速开始

NapCat HTTP API 的服务地址由部署配置决定；下表中的路径均为相对路径。接口文档主要使用 JSON 请求体，示例通用响应如下：

```json
{
  "status": "ok",
  "retcode": 0,
  "data": {},
  "message": "",
  "wording": ""
}
```

例如，发送私聊或群聊消息使用 `POST /send_msg`，请求体至少需要 `message`；再按消息类型提供 `user_id` 或 `group_id`。详见[发送消息](https://napcat.apifox.cn/226656652e0.md)。

## 接口目录

### 核心接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/set_group_todo` | [设置群待办](https://napcat.apifox.cn/395460568e0.md) | 将指定消息设置为群待办 |
| `POST` | `/complete_group_todo` | [完成群待办](https://napcat.apifox.cn/444247697e0.md) | 将指定消息对应的群待办标记为已完成 |
| `POST` | `/cancel_group_todo` | [取消群待办](https://napcat.apifox.cn/444247698e0.md) | 将指定消息对应的群待办取消 |
| `POST` | `/group_poke` | [发送戳一戳](https://napcat.apifox.cn/226659265e0.md) | 在群聊或私聊中发送戳一戳动作 |
| `POST` | `/friend_poke` | [发送戳一戳](https://napcat.apifox.cn/226659255e0.md) | 在群聊或私聊中发送戳一戳动作 |
| `POST` | `/send_poke` | [发送戳一戳](https://napcat.apifox.cn/250286923e0.md) | 在群聊或私聊中发送戳一戳动作 |

### 扩展接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/set_group_kick_members` | [批量踢出群成员](https://napcat.apifox.cn/301542209e0.md) | 从指定群聊中批量踢出多个成员 |
| `POST` | `/create_collection` | [创建收藏](https://napcat.apifox.cn/226659178e0.md) | - |
| `POST` | `/set_self_longnick` | [设置个性签名](https://napcat.apifox.cn/226659186e0.md) | 修改当前登录帐号的个性签名 |
| `POST` | `/set_qq_avatar` | [设置QQ头像](https://napcat.apifox.cn/226658980e0.md) | 修改当前账号的QQ头像 |
| `POST` | `/translate_en2zh` | [英文单词翻译](https://napcat.apifox.cn/226659102e0.md) | 将英文单词列表翻译为中文 |
| `POST` | `/get_clientkey` | [获取ClientKey](https://napcat.apifox.cn/250286915e0.md) | 获取当前登录帐号的ClientKey |
| `POST` | `/ocr_image` | [图片 OCR 识别](https://napcat.apifox.cn/226658231e0.md) | 识别图片中的文字内容(仅Windows端支持) |
| `POST` | `/.ocr_image` | [图片 OCR 识别 (内部)](https://napcat.apifox.cn/226658234e0.md) | 识别图片中的文字内容(仅Windows端支持) |
| `POST` | `/set_group_special_title` | [设置专属头衔](https://napcat.apifox.cn/226656931e0.md) | 设置群聊中指定成员的专属头衔 |
| `POST` | `/get_ai_characters` | [获取AI角色列表](https://napcat.apifox.cn/229485683e0.md) | 获取群聊中的AI角色列表 |
| `POST` | `/send_qzone_msg` | [发表QQ空间说说](https://napcat.apifox.cn/496813058e0.md) | 在QQ空间(Qzone)发表说说, 支持纯文字或带(多)图, 可设置查看权限 |
| `POST` | `/delete_qzone_msg` | [删除QQ空间说说](https://napcat.apifox.cn/496813059e0.md) | 删除QQ空间(Qzone)的一条说说, 按 tid 删除 |

### 流式传输扩展

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/clean_stream_temp_file` | [清理流式传输临时文件](https://napcat.apifox.cn/395354124e0.md) | - |
| `POST` | `/download_file_record_stream` | [下载语音文件流](https://napcat.apifox.cn/395417040e0.md) | - |
| `POST` | `/download_file_image_stream` | [下载图片文件流](https://napcat.apifox.cn/395419462e0.md) | - |
| `POST` | `/test_download_stream` | [测试下载流](https://napcat.apifox.cn/395355338e0.md) | - |

### 流式接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/download_file_stream` | [下载文件流](https://napcat.apifox.cn/395413859e0.md) | 以流式方式从网络或本地下载文件 |
| `POST` | `/upload_file_stream` | [上传文件流](https://napcat.apifox.cn/395363988e0.md) | 以流式方式上传文件数据到机器人 |

### 频道接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/get_guild_list` | [获取频道列表](https://napcat.apifox.cn/226659311e0.md) | 获取当前帐号已加入的频道列表 |
| `POST` | `/get_guild_service_profile` | [获取频道个人信息](https://napcat.apifox.cn/226659317e0.md) | 获取当前帐号在频道中的个人资料 |

### 群组接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/get_group_detail_info` | [获取群详细信息](https://napcat.apifox.cn/307180859e0.md) | 获取群聊的详细信息，包括成员数、最大成员数等 |
| `POST` | `/get_group_list` | [获取群列表](https://napcat.apifox.cn/226656992e0.md) | 获取当前帐号的群聊列表 |
| `POST` | `/get_group_info` | [获取群信息](https://napcat.apifox.cn/226656979e0.md) | 获取群聊的基本信息 |
| `POST` | `/get_group_member_list` | [获取群成员列表](https://napcat.apifox.cn/226657034e0.md) | 获取群聊中的所有成员列表 |
| `POST` | `/get_group_member_info` | [获取群成员信息](https://napcat.apifox.cn/226657019e0.md) | 获取群聊中指定成员的信息 |
| `POST` | `/send_group_msg` | [发送群消息](https://napcat.apifox.cn/226656598e0.md) | 发送群消息 |
| `POST` | `/set_group_add_request` | [处理加群请求](https://napcat.apifox.cn/226656947e0.md) | 同意或拒绝加群请求或邀请 |
| `POST` | `/set_group_leave` | [退出群组](https://napcat.apifox.cn/226656926e0.md) | 退出或解散指定群聊 |
| `POST` | `/set_group_whole_ban` | [全员禁言](https://napcat.apifox.cn/226656802e0.md) | 开启或关闭指定群聊的全员禁言 |
| `POST` | `/set_group_ban` | [群组禁言](https://napcat.apifox.cn/226656791e0.md) | 禁言群聊中的指定成员 |
| `POST` | `/set_group_kick` | [群组踢人](https://napcat.apifox.cn/226656748e0.md) | 将指定成员踢出群聊 |
| `POST` | `/set_group_admin` | [设置群管理员](https://napcat.apifox.cn/226656815e0.md) | 设置或取消群聊中的管理员 |
| `POST` | `/set_group_name` | [设置群名称](https://napcat.apifox.cn/226656919e0.md) | 修改指定群聊的名称 |
| `POST` | `/set_group_card` | [设置群名片](https://napcat.apifox.cn/226656913e0.md) | 设置群聊中指定成员的群名片 |
| `POST` | `/_get_group_notice` | [获取群公告](https://napcat.apifox.cn/226658742e0.md) | 获取指定群聊中的公告列表 |
| `POST` | `/get_essence_msg_list` | [获取群精华消息](https://napcat.apifox.cn/226658664e0.md) | 获取指定群聊中的精华消息列表 |
| `POST` | `/get_group_ignored_notifies` | [获取群忽略通知](https://napcat.apifox.cn/226659323e0.md) | 获取被忽略的入群申请和邀请通知 |
| `POST` | `/delete_essence_msg` | [移出精华消息](https://napcat.apifox.cn/226658678e0.md) | 将一条消息从群精华消息列表中移出 |
| `POST` | `/set_essence_msg` | [设置精华消息](https://napcat.apifox.cn/226658674e0.md) | 将一条消息设置为群精华消息 |
| `POST` | `/_del_group_notice` | [删除群公告](https://napcat.apifox.cn/226659240e0.md) | 删除群聊中的公告 |
| `POST` | `/get_group_shut_list` | [获取群禁言列表](https://napcat.apifox.cn/226659300e0.md) | - |
| `POST` | `/get_group_ignore_add_request` | [获取群被忽略的加群请求](https://napcat.apifox.cn/226659234e0.md) | - |
| `POST` | `/set_group_member_invite_policy` | [设置群成员邀请策略](https://napcat.apifox.cn/496813055e0.md) | 设置是否允许群成员邀请好友进群，以及邀请是否需要管理员审核 |
| `POST` | `/set_group_member_permissions` | [设置群成员功能权限](https://napcat.apifox.cn/496813056e0.md) | 设置群成员上传相册、发起临时会话和发起新群聊的权限；未传入的项目保持不变 |
| `POST` | `/set_group_new_member_history_visibility` | [设置新成员历史消息可见性](https://napcat.apifox.cn/496813057e0.md) | 设置新入群成员默认是否可以查看最近聊天记录 |

### 群组扩展

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/del_group_album_media` | [删除群相册媒体](https://napcat.apifox.cn/395455119e0.md) | - |
| `POST` | `/set_group_album_media_like` | [点赞群相册媒体](https://napcat.apifox.cn/395457331e0.md) | - |
| `POST` | `/cancel_group_album_media_like` | [取消点赞群相册媒体](https://napcat.apifox.cn/462330906e0.md) | - |
| `POST` | `/do_group_album_comment` | [发表群相册评论](https://napcat.apifox.cn/395458911e0.md) | - |
| `POST` | `/get_group_album_media_list` | [获取群相册媒体列表](https://napcat.apifox.cn/395459066e0.md) | - |
| `POST` | `/get_qun_album_list` | [获取群相册列表](https://napcat.apifox.cn/395460287e0.md) | - |
| `POST` | `/upload_image_to_qun_album` | [上传图片到群相册](https://napcat.apifox.cn/395459739e0.md) | - |
| `POST` | `/set_group_add_option` | [设置群加群选项](https://napcat.apifox.cn/301542178e0.md) | - |
| `POST` | `/set_group_robot_add_option` | [设置群机器人加群选项](https://napcat.apifox.cn/301542198e0.md) | - |
| `POST` | `/set_group_search` | [设置群搜索选项](https://napcat.apifox.cn/301542170e0.md) | - |
| `POST` | `/set_group_remark` | [设置群备注](https://napcat.apifox.cn/283136268e0.md) | 设置群备注 |
| `POST` | `/get_group_info_ex` | [获取群详细信息 (扩展)](https://napcat.apifox.cn/226659229e0.md) | - |
| `POST` | `/set_group_sign` | [群打卡](https://napcat.apifox.cn/226659329e0.md) | - |
| `POST` | `/send_group_sign` | [群打卡](https://napcat.apifox.cn/230897177e0.md) | - |
| `POST` | `/get_group_signed_list` | [获取群组今日打卡列表](https://napcat.apifox.cn/467693191e0.md) | - |
| `POST` | `/get_group_share_link` | [获取群分享链接](https://napcat.apifox.cn/514638630e0.md) | 通过 NTQQ 原生 getJoinGroupLink 获取群的 H5 分享/加群链接 (qm.qq.com/q/xxx 或 qun.qq.com/universal-share/share?...) |

### 文件接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/get_file` | [获取文件](https://napcat.apifox.cn/226658985e0.md) | 获取指定文件的详细信息及下载路径 |
| `POST` | `/get_image` | [获取图片](https://napcat.apifox.cn/226657066e0.md) | 获取指定图片的信息及路径 |
| `POST` | `/get_record` | [获取语音](https://napcat.apifox.cn/226657058e0.md) | 获取指定语音文件的信息，并支持格式转换 |
| `POST` | `/get_group_file_url` | [获取群文件URL](https://napcat.apifox.cn/226658867e0.md) | 获取指定群文件的下载链接 |
| `POST` | `/get_private_file_url` | [获取私聊文件URL](https://napcat.apifox.cn/266151849e0.md) | 获取指定私聊文件的下载链接 |

### 文件扩展

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/move_group_file` | [移动群文件](https://napcat.apifox.cn/283136359e0.md) | - |
| `POST` | `/rename_group_file` | [重命名群文件](https://napcat.apifox.cn/283136375e0.md) | - |
| `POST` | `/trans_group_file` | [传输群文件](https://napcat.apifox.cn/283136366e0.md) | - |
| `POST` | `/create_flash_task` | [创建闪传任务](https://napcat.apifox.cn/410334666e0.md) | - |
| `POST` | `/get_flash_file_list` | [获取闪传文件列表](https://napcat.apifox.cn/410334667e0.md) | - |
| `POST` | `/get_flash_file_url` | [获取闪传文件链接](https://napcat.apifox.cn/410334668e0.md) | - |
| `POST` | `/send_flash_msg` | [发送闪传消息](https://napcat.apifox.cn/410334669e0.md) | - |
| `POST` | `/get_share_link` | [获取文件分享链接](https://napcat.apifox.cn/410334670e0.md) | - |
| `POST` | `/get_fileset_info` | [获取文件集信息](https://napcat.apifox.cn/410334671e0.md) | - |
| `POST` | `/get_online_file_msg` | [获取在线文件消息](https://napcat.apifox.cn/410334672e0.md) | - |
| `POST` | `/send_online_file` | [发送在线文件](https://napcat.apifox.cn/410334673e0.md) | - |
| `POST` | `/send_online_folder` | [发送在线文件夹](https://napcat.apifox.cn/410334674e0.md) | - |
| `POST` | `/receive_online_file` | [接收在线文件](https://napcat.apifox.cn/410334675e0.md) | - |
| `POST` | `/refuse_online_file` | [拒绝在线文件](https://napcat.apifox.cn/410334676e0.md) | - |
| `POST` | `/cancel_online_file` | [取消在线文件](https://napcat.apifox.cn/410334677e0.md) | - |
| `POST` | `/download_fileset` | [下载文件集](https://napcat.apifox.cn/410334678e0.md) | - |
| `POST` | `/get_fileset_id` | [获取文件集 ID](https://napcat.apifox.cn/410334679e0.md) | - |

### 系统接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/set_doubt_friends_add_request` | [处理可疑好友申请](https://napcat.apifox.cn/289565525e0.md) | 同意或拒绝系统的可疑好友申请 |
| `POST` | `/get_doubt_friends_add_request` | [获取可疑好友申请](https://napcat.apifox.cn/289565516e0.md) | 获取系统的可疑好友申请列表 |
| `POST` | `/get_login_info` | [获取登录号信息](https://napcat.apifox.cn/226656952e0.md) | 获取当前登录帐号的信息 |
| `POST` | `/get_version_info` | [获取版本信息](https://napcat.apifox.cn/226657087e0.md) | 获取版本信息 |
| `POST` | `/can_send_record` | [是否可以发送语音](https://napcat.apifox.cn/226657080e0.md) | 检查是否可以发送语音 |
| `POST` | `/can_send_image` | [是否可以发送图片](https://napcat.apifox.cn/226657071e0.md) | 检查是否可以发送图片 |
| `POST` | `/get_status` | [获取运行状态](https://napcat.apifox.cn/226657083e0.md) | 获取运行状态 |
| `POST` | `/get_csrf_token` | [获取 CSRF Token](https://napcat.apifox.cn/226657044e0.md) | 获取 CSRF Token |
| `POST` | `/get_credentials` | [获取登录凭证](https://napcat.apifox.cn/226657054e0.md) | 获取登录凭证 |
| `POST` | `/nc_get_packet_status` | [获取Packet状态](https://napcat.apifox.cn/226659280e0.md) | 获取底层Packet服务的运行状态 |
| `POST` | `/set_restart` | [重启服务](https://napcat.apifox.cn/410334662e0.md) | 重启服务 |
| `POST` | `/get_group_system_msg` | [获取群系统消息](https://napcat.apifox.cn/226658660e0.md) | 获取群系统消息 |
| `POST` | `/clean_cache` | [清理缓存](https://napcat.apifox.cn/298305106e0.md) | 清理缓存 |

### 系统扩展

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/get_rkey` | [获取扩展 RKey](https://napcat.apifox.cn/283136230e0.md) | - |
| `POST` | `/get_rkey_server` | [获取 RKey 服务器](https://napcat.apifox.cn/283136236e0.md) | - |
| `POST` | `/set_online_status` | [设置在线状态](https://napcat.apifox.cn/226658977e0.md) | - |
| `POST` | `/get_robot_uin_range` | [获取机器人 UIN 范围](https://napcat.apifox.cn/226658975e0.md) | - |
| `POST` | `/fetch_custom_face` | [获取自定义表情](https://napcat.apifox.cn/226659210e0.md) | - |
| `POST` | `/fetch_custom_face_detail` | [获取自定义表情详情](https://napcat.apifox.cn/467693192e0.md) | - |
| `POST` | `/add_custom_face` | [添加自定义表情](https://napcat.apifox.cn/467693193e0.md) | - |
| `POST` | `/delete_custom_face` | [删除自定义表情](https://napcat.apifox.cn/467693194e0.md) | - |
| `POST` | `/set_custom_face_desc` | [修改自定义表情描述](https://napcat.apifox.cn/467693195e0.md) | - |
| `POST` | `/set_input_status` | [设置输入状态](https://napcat.apifox.cn/226659225e0.md) | - |
| `POST` | `/nc_get_user_status` | [获取用户在线状态](https://napcat.apifox.cn/226659292e0.md) | - |
| `POST` | `/nc_get_rkey` | [获取 RKey](https://napcat.apifox.cn/226659297e0.md) | - |
| `POST` | `/get_mini_app_ark` | [获取小程序 Ark](https://napcat.apifox.cn/227738594e0.md) | - |
| `POST` | `/send_packet` | [发送原始数据包](https://napcat.apifox.cn/250286903e0.md) | - |
| `POST` | `/bot_exit` | [退出登录](https://napcat.apifox.cn/283136399e0.md) | - |
| `POST` | `/get_collection_list` | [获取收藏列表](https://napcat.apifox.cn/226659182e0.md) | - |

### 消息接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/forward_friend_single_msg` | [转发单条消息](https://napcat.apifox.cn/226659051e0.md) | 转发单条消息 |
| `POST` | `/forward_group_single_msg` | [转发单条消息](https://napcat.apifox.cn/226659074e0.md) | 转发单条消息 |
| `POST` | `/mark_group_msg_as_read` | [标记群聊已读](https://napcat.apifox.cn/226659167e0.md) | 标记指定渠道的消息为已读 |
| `POST` | `/mark_private_msg_as_read` | [标记私聊已读](https://napcat.apifox.cn/226659165e0.md) | 标记指定渠道的消息为已读 |
| `POST` | `/get_msg` | [获取消息](https://napcat.apifox.cn/226656707e0.md) | 根据消息 ID 获取消息详细信息 |
| `POST` | `/send_private_msg` | [发送私聊消息](https://napcat.apifox.cn/226656553e0.md) | 发送私聊消息 |
| `POST` | `/send_msg` | [发送消息](https://napcat.apifox.cn/226656652e0.md) | 发送私聊或群聊消息 |
| `POST` | `/delete_msg` | [撤回消息](https://napcat.apifox.cn/226919954e0.md) | 撤回已发送的消息 |
| `POST` | `/mark_msg_as_read` | [标记消息已读 (Go-CQHTTP)](https://napcat.apifox.cn/226657389e0.md) | 标记指定渠道的消息为已读 |
| `POST` | `/_mark_all_as_read` | [标记所有消息已读](https://napcat.apifox.cn/226659194e0.md) | - |

### 消息扩展

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/fetch_emoji_like` | [获取表情点赞详情](https://napcat.apifox.cn/226659219e0.md) | - |
| `POST` | `/get_emoji_likes` | [获取消息表情点赞列表](https://napcat.apifox.cn/410334663e0.md) | - |
| `POST` | `/fetch_ptt_text` | [获取语音转文字结果](https://napcat.apifox.cn/458248103e0.md) | - |
| `POST` | `/ArkShareGroup` | [分享群 (Ark)](https://napcat.apifox.cn/226658971e0.md) | 获取群分享的 Ark 内容 |
| `POST` | `/ArkSharePeer` | [分享用户 (Ark)](https://napcat.apifox.cn/226658965e0.md) | 获取用户推荐的 Ark 内容 |
| `POST` | `/send_group_ark_share` | [分享群 (Ark)](https://napcat.apifox.cn/410334664e0.md) | 获取群分享的 Ark 内容 |
| `POST` | `/send_ark_share` | [分享用户 (Ark)](https://napcat.apifox.cn/410334665e0.md) | 获取用户推荐的 Ark 内容 |
| `POST` | `/set_msg_emoji_like` | [设置消息表情点赞](https://napcat.apifox.cn/226659104e0.md) | - |
| `POST` | `/click_inline_keyboard_button` | [点击内联键盘按钮](https://napcat.apifox.cn/266151864e0.md) | - |

### 用户接口

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/set_friend_remark` | [设置好友备注](https://napcat.apifox.cn/298305173e0.md) | 设置好友备注 |
| `POST` | `/send_like` | [点赞](https://napcat.apifox.cn/226656717e0.md) | 给指定用户点赞 |
| `POST` | `/get_friend_list` | [获取好友列表](https://napcat.apifox.cn/226656976e0.md) | 获取当前帐号的好友列表 |
| `POST` | `/set_friend_add_request` | [处理加好友请求](https://napcat.apifox.cn/226656932e0.md) | 同意或拒绝加好友请求 |
| `POST` | `/get_cookies` | [获取 Cookies](https://napcat.apifox.cn/226657041e0.md) | 获取指定域名的 Cookies |
| `POST` | `/get_recent_contact` | [获取最近会话](https://napcat.apifox.cn/226659190e0.md) | 获取最近会话 |

### 用户扩展

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/get_friends_with_category` | [获取带分组的好友列表](https://napcat.apifox.cn/226658978e0.md) | - |
| `POST` | `/get_profile_like` | [获取资料点赞](https://napcat.apifox.cn/226659197e0.md) | - |
| `POST` | `/set_diy_online_status` | [设置自定义在线状态](https://napcat.apifox.cn/266151905e0.md) | 设置自定义在线状态 |
| `POST` | `/get_unidirectional_friend_list` | [获取单向好友列表](https://napcat.apifox.cn/266151878e0.md) | - |

### AI 扩展

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/get_ai_record` | [获取 AI 语音](https://napcat.apifox.cn/229486818e0.md) | 通过 AI 语音引擎获取指定文本的语音 URL |
| `POST` | `/send_group_ai_record` | [发送群 AI 语音](https://napcat.apifox.cn/229486774e0.md) | 发送 AI 生成的语音到指定群聊 |

### Go-CQHTTP

| 方法 | 路径 | 接口 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/set_qq_profile` | [设置QQ资料](https://napcat.apifox.cn/226657374e0.md) | 修改当前账号的昵称、个性签名等资料 |
| `POST` | `/get_group_root_files` | [获取群根目录文件列表](https://napcat.apifox.cn/226658823e0.md) | 获取群文件根目录下的所有文件和文件夹 |
| `POST` | `/delete_friend` | [删除好友](https://napcat.apifox.cn/227237873e0.md) | 从好友列表中删除指定用户 |
| `POST` | `/check_url_safely` | [检查URL安全性](https://napcat.apifox.cn/228534361e0.md) | 检查指定URL的安全等级 |
| `POST` | `/get_online_clients` | [获取在线客户端](https://napcat.apifox.cn/226657379e0.md) | 获取当前登录账号的在线客户端列表 |
| `POST` | `/get_group_honor_info` | [获取群荣誉信息](https://napcat.apifox.cn/226657036e0.md) | 获取指定群聊的荣誉信息，如龙王等 |
| `POST` | `/_send_group_notice` | [发送群公告](https://napcat.apifox.cn/226658740e0.md) | 在指定群聊中发布新的公告 |
| `POST` | `/get_group_at_all_remain` | [获取群艾特全体剩余次数](https://napcat.apifox.cn/227245941e0.md) | 获取指定群聊中艾特全体成员的剩余次数 |
| `POST` | `/send_forward_msg` | [发送合并转发消息](https://napcat.apifox.cn/226659136e0.md) | 发送合并转发消息 |
| `POST` | `/send_group_forward_msg` | [发送群合并转发消息](https://napcat.apifox.cn/226657396e0.md) | - |
| `POST` | `/send_private_forward_msg` | [发送私聊合并转发消息](https://napcat.apifox.cn/226657399e0.md) | - |
| `POST` | `/get_stranger_info` | [获取陌生人信息](https://napcat.apifox.cn/226656970e0.md) | 获取指定非好友用户的信息 |
| `POST` | `/download_file` | [下载文件](https://napcat.apifox.cn/226658887e0.md) | 下载网络文件到本地临时目录 |
| `POST` | `/upload_group_file` | [上传群文件](https://napcat.apifox.cn/226658753e0.md) | 上传资源路径或URL指定的文件到指定群聊的文件系统中 |
| `POST` | `/get_group_msg_history` | [获取群历史消息](https://napcat.apifox.cn/226657401e0.md) | 获取指定群聊的历史聊天记录 |
| `POST` | `/get_forward_msg` | [获取合并转发消息](https://napcat.apifox.cn/226656712e0.md) | 获取合并转发消息的具体内容 |
| `POST` | `/get_friend_msg_history` | [获取好友历史消息](https://napcat.apifox.cn/226659174e0.md) | 获取指定好友的历史聊天记录 |
| `POST` | `/.handle_quick_operation` | [处理快速操作](https://napcat.apifox.cn/226658889e0.md) | 处理来自事件上报的快速操作请求 |
| `POST` | `/set_group_portrait` | [设置群头像](https://napcat.apifox.cn/226658669e0.md) | 修改指定群聊的头像 |
| `POST` | `/upload_private_file` | [上传私聊文件](https://napcat.apifox.cn/226658883e0.md) | 上传本地文件到指定私聊会话中 |
| `POST` | `/_get_model_show` | [获取机型显示](https://napcat.apifox.cn/227233981e0.md) | 获取当前账号可用的设备机型显示名称列表 |
| `POST` | `/_set_model_show` | [设置机型](https://napcat.apifox.cn/227233993e0.md) | 设置当前账号的设备机型名称 |
| `POST` | `/delete_group_file` | [删除群文件](https://napcat.apifox.cn/226658755e0.md) | 在群文件系统中删除指定的文件 |
| `POST` | `/create_group_file_folder` | [创建群文件目录](https://napcat.apifox.cn/226658773e0.md) | 在群文件系统中创建新的文件夹 |
| `POST` | `/delete_group_folder` | [删除群文件目录](https://napcat.apifox.cn/226658779e0.md) | 在群文件系统中删除指定的文件夹 |
| `POST` | `/get_group_file_system_info` | [获取群文件系统信息](https://napcat.apifox.cn/226658789e0.md) | 获取群聊文件系统的空间及状态信息 |
| `POST` | `/get_group_files_by_folder` | [获取群文件夹文件列表](https://napcat.apifox.cn/226658865e0.md) | 获取指定群文件夹下的文件及子文件夹列表 |

## 数据模型

| 模型 | 说明 |
| --- | --- |
| [BaseResponse](https://napcat.apifox.cn/246111186d0.md) | - |
| [EmptyData](https://napcat.apifox.cn/246111187d0.md) | - |
| [FileBaseData](https://napcat.apifox.cn/246111188d0.md) | - |
| [OB11MessageAt](https://napcat.apifox.cn/246111189d0.md) | - |
| [OB11MessageContact](https://napcat.apifox.cn/246111190d0.md) | - |
| [OB11MessageCustomMusic](https://napcat.apifox.cn/246111191d0.md) | - |
| [OB11MessageData](https://napcat.apifox.cn/246111192d0.md) | - |
| [OB11MessageDice](https://napcat.apifox.cn/246111193d0.md) | - |
| [OB11MessageFace](https://napcat.apifox.cn/246111194d0.md) | - |
| [OB11MessageFileBase](https://napcat.apifox.cn/246111195d0.md) | - |
| [OB11MessageFile](https://napcat.apifox.cn/246111196d0.md) | - |
| [OB11MessageFlashTransfer](https://napcat.apifox.cn/246111197d0.md) | - |
| [OB11MessageForward](https://napcat.apifox.cn/246111198d0.md) | - |
| [OB11MessageIdMusic](https://napcat.apifox.cn/246111199d0.md) | - |
| [OB11MessageImage](https://napcat.apifox.cn/246111200d0.md) | - |
| [OB11MessageJson](https://napcat.apifox.cn/246111201d0.md) | - |
| [OB11MessageLocation](https://napcat.apifox.cn/246111202d0.md) | - |
| [OB11MessageMFace](https://napcat.apifox.cn/246111203d0.md) | - |
| [OB11MessageMarkdown](https://napcat.apifox.cn/246111204d0.md) | - |
| [OB11MessageMiniApp](https://napcat.apifox.cn/246111205d0.md) | - |
| [OB11MessageMixType](https://napcat.apifox.cn/246111206d0.md) | - |
| [OB11MessageNode](https://napcat.apifox.cn/246111207d0.md) | - |
| [OB11MessageOnlineFile](https://napcat.apifox.cn/246111208d0.md) | - |
| [OB11MessagePoke](https://napcat.apifox.cn/246111209d0.md) | - |
| [OB11MessageRPS](https://napcat.apifox.cn/246111210d0.md) | - |
| [OB11MessageRecord](https://napcat.apifox.cn/246111211d0.md) | - |
| [OB11MessageReply](https://napcat.apifox.cn/246111212d0.md) | - |
| [OB11Message](https://napcat.apifox.cn/246111213d0.md) | - |
| [OB11MessageText](https://napcat.apifox.cn/246111214d0.md) | - |
| [OB11MessageVideo](https://napcat.apifox.cn/246111215d0.md) | - |
| [OB11MessageXml](https://napcat.apifox.cn/246111216d0.md) | - |
| [OB11PostSendMsg](https://napcat.apifox.cn/246111217d0.md) | - |
| [OB11Sender](https://napcat.apifox.cn/246111218d0.md) | - |
| [OB11GroupMember](https://napcat.apifox.cn/246111219d0.md) | - |
| [OB11Group](https://napcat.apifox.cn/246111220d0.md) | - |
| [OB11ActionMessage](https://napcat.apifox.cn/246111221d0.md) | - |
| [OB11Notify](https://napcat.apifox.cn/246111222d0.md) | - |
| [OB11User](https://napcat.apifox.cn/246111223d0.md) | - |
| [OB11LatestMessage](https://napcat.apifox.cn/246111224d0.md) | - |

## 维护说明

本索引是一次性快照。NapCat 官方文档说明其内容会随项目更新自动生成，因此新增接口、字段、返回码和兼容行为应以官方详情页为准。
