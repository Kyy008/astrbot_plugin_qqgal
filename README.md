# astrbot_plugin_qqgal v2.0.0
基于 AstrBot 的 GalGame 风格“选项生成 + 图片渲染 + 立绘叠加”插件（Napcat/OneBot11 测试通过）。

> 本项目基于 [bvzrays/astrbot_plugin_qqgal](https://github.com/bvzrays/astrbot_plugin_qqgal) 继续维护和改进，核心创意与基础实现来自原项目。感谢原作者 **bvzrays** 的开发与开源贡献。

> 在聊天中“引用消息”作为语境，生成 A/B/C… 多分支选项，并渲染为 Gal UI 图片；支持自动生成人物立绘、抠色并叠加到背景。
![6f0b057fd7b4d9e2b118aab88cc1bff6](https://github.com/user-attachments/assets/eae2a831-ebff-4e3b-9e3e-ef071278faa3)
<img width="406" height="624" alt="image" src="https://github.com/user-attachments/assets/17f93a7d-5d64-4e25-aba9-169d1ebb78b6" />

## 新特性（2.0.0）
- 新增自动生图 + 抠色：
  - 使用被引用对象头像作为参考，调用 Gemini 原生端点生成立绘，若未成功扣掉背景则在配置中加大抠色范围 `chroma_tolerance`；
  - 生图背景为亮绿纯色（可配），本地自动采样实际绿幕色，结合边缘连通区、欧氏距离阈值和羽化边缘产出透明 PNG；
  - 立绘缓存：`charactert/QQ-matte.png`，下次直接复用。
- 结构与层级优化：背景 < 立绘 < 玻璃层 < 文本；引用区与头像昵称清晰可见。
- 配置项精简：启用立绘（默认开）、Key 列表、反代地址、抠色参数与立绘位置即可。

### 对原项目的修复
- 修正 Gemini 生图模型配置，确保请求实际使用 `gemini_model`，不再被隐藏的旧端点配置覆盖。
- 改进绿幕抠图：自动识别实际绿幕色，通过边缘连通区域区分背景，并保留原图已有透明度。
- 增加抠图结果与立绘缓存校验，避免异常抠图或无透明通道的旧缓存直接参与合成。
- 改善 Gemini 端点和返回字段兼容性，支持常见的反代地址及 MIME 字段格式。

> 第一次生成立绘会比较慢，之后会直接调用已保存的立绘。头像立绘会存储在插件目录中，可使用 `/刷新立绘` 重新生成。

## 指令 🗂️
- `/选项`：生成 A/B/C… 多分支选项，并渲染为 Gal UI 图片。指令后文本优先作为语境；引用消息时则读取被回复文本。
- `/刷新立绘`：刷新自己的立绘。

## 安装与使用 🚀
1. 安装 AstrBot。
2. 将仓库放入 `AstrBot/data/plugins/astrbot_plugin_qqgal/`。
3. WebUI → 插件管理：启用并填写 Key、反代地址等。
4. 协议端推荐 Napcat（OneBot 11）。

## 配置要点 📋
- 立绘：`enable_character`（默认开）
- Key：`gemini_api_keys`（列表，多 Key 轮询）
- 反代：`gemini_base_url`（空则走官方）
- 生图模型：`gemini_model`（直接用于 Gemini `generateContent` 端点）
- 抠色：`chroma_bg_color`（默认 #00FF00）、`chroma_tolerance`（默认 80）
- 位置尺寸：`character_scale`、`character_bottom_offset`、`character_x_offset`

## 资源（背景图） 🖼️
- 将图片放入 `background/`；渲染时随机选择：
  - 底层：`cover+blur` 铺满；
  - 顶层：`contain` 等比居中。

## 工作流程 🧭
1. 提取语境（文本/引用）。
2. 生成并规范化选项。
3. 生图（可选）→ 抠色 → 写入 `QQ-matte.png` → 叠加立绘 → 合成输出。

## 兼容性 🔌
- 框架：AstrBot
- 协议端：Napcat（OneBot 11）

## 许可 📄
本项目沿用原项目的 AGPL-3.0 许可证。请确保背景、头像等素材拥有合法的使用权限。

## 致谢 🙏
- 原项目：[bvzrays/astrbot_plugin_qqgal](https://github.com/bvzrays/astrbot_plugin_qqgal)
- AstrBot 项目与社区
- Napcat / OneBot 生态
