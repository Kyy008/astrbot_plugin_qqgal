# astrbot_plugin_qqgal

基于 AstrBot 的 GalGame 风格选项生成与图片渲染插件，已在 NapCat / OneBot 11 环境中测试。

在聊天中发送文本或引用一条消息，插件会根据语境生成 A/B/C 等分支选项，并渲染成带背景、人物立绘和对话 UI 的图片。

## 项目来源与致谢

本项目基于 [bvzrays/astrbot_plugin_qqgal](https://github.com/bvzrays/astrbot_plugin_qqgal) 继续维护和改进，核心创意、基础功能及早期实现均来自原项目。本仓库并非从零开始的原创项目，感谢原作者 **bvzrays** 的开发与开源贡献。

### 相比原项目的改进

- 修正 Gemini 生图模型配置：请求会实际使用 WebUI 中设置的 `gemini_model`，不再被隐藏的旧端点配置覆盖。
- 改善 Gemini 端点兼容性：兼容基础地址中包含 `v1` 或 `v1beta` 的情况，并兼容常见的图片 MIME 字段格式。
- 改进绿幕抠图：从图片边缘自动估计实际绿幕色，通过边缘连通区域识别背景，并清理被发丝等前景包围的强绿色区域。
- 增加抠图结果校验：保留原图已有透明度，检测透明区域比例，避免把未成功抠图或异常结果写入缓存并叠加到画面。
- 增加立绘缓存校验：发现不含有效透明通道的旧缓存时自动忽略并重新生成。

## 功能

- 根据命令文本或引用消息生成多个 GalGame 风格选项。
- 将背景、立绘、引用内容、头像昵称和选项合成为图片。
- 使用被引用对象的头像作为参考，通过 Gemini 生成二次元人物立绘。
- 自动完成绿幕抠图、立绘标准化和本地缓存。
- 支持多个 Gemini API Key 轮询和自定义反代地址。

## 指令

- `/选项 [文本]`：根据命令后的文本生成选项；引用消息时也可直接使用被引用内容作为语境。
- `/刷新立绘`：删除并重新生成自己的立绘缓存。

第一次生成立绘需要调用生图接口，耗时会相对较长；后续会直接使用 `charactert/` 中的本地缓存。

## 安装

1. 安装并运行 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。
2. 将本仓库放入 `AstrBot/data/plugins/astrbot_plugin_qqgal/`。
3. 在 AstrBot WebUI 的插件管理中启用插件并完成配置。
4. 推荐使用 NapCat / OneBot 11 协议端。

## 主要配置

| 配置项 | 说明 |
| --- | --- |
| `enable_character` | 是否启用人物立绘生成与叠加 |
| `gemini_api_keys` | Gemini API Key 列表，支持依次轮询 |
| `gemini_base_url` | Gemini API 基础地址；留空时使用官方地址 |
| `gemini_model` | 用于人物生图的 Gemini 模型名 |
| `chroma_bg_color` | 绿幕基准颜色，默认 `#00FF00` |
| `chroma_tolerance` | 抠色容差，数值越大匹配范围越宽 |
| `character_scale` | 立绘在最终画面中的缩放比例 |
| `character_x_offset` | 立绘水平偏移量 |

更多选项可在 AstrBot WebUI 的插件配置页面中查看。

## 背景资源

将 JPG、PNG 或 WebP 图片放入 `background/`。渲染时会随机选择一张图片，以模糊铺满层和等比居中层组合成背景。

请确保自行添加的背景、头像及其他素材拥有合法的使用权限。

## 许可

本项目沿用原项目的 [AGPL-3.0 License](LICENSE)。使用、修改或分发时请遵守许可证要求，并保留原项目及贡献者的署名信息。

## 相关项目

- 原项目：[bvzrays/astrbot_plugin_qqgal](https://github.com/bvzrays/astrbot_plugin_qqgal)
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- NapCat / OneBot 生态
