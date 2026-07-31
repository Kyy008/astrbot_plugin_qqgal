from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

from typing import Dict, Any, List
import asyncio
import base64
import mimetypes
import html as html_lib
import os
import random
import aiohttp
from collections import deque
from io import BytesIO


@register("astrbot_plugin_qqgal", "bvzrays", "引用文本生成 GalGame 风格选项", "2.0.0")
class QQGalPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self._cfg_obj = config or {}
        # 背景目录就绪
        try:
            base_dir = os.path.dirname(__file__)
            bg_dir = os.path.join(base_dir, str(self.cfg().get("background_dir", "background")))
            os.makedirs(bg_dir, exist_ok=True)
        except Exception as e:
            logger.error("[qqgal] init background dir failed: %s", e)

    def cfg(self) -> Dict[str, Any]:
        try:
            return self._cfg_obj if self._cfg_obj is not None else {}
        except Exception as e:
            logger.error("[qqgal] read config failed: %s", e)
            return {}

    async def _extract_quoted_text(self, event: AstrMessageEvent) -> str:
        """获取作为选项依据的原文：
        1) 若消息携带文本参数，优先使用参数文本（指令词后内容）。
        2) 若为引用消息（OneBot v11/Napcat），尝试通过 get_msg 拉取被回复消息的纯文本。
        3) 否则返回空串。
        """
        # 1) 文本参数
        try:
            text = (event.message_str or "").strip()
            prefixes = ("/选项", "选项", "/gal", "gal", "/gal选项", "gal选项")
            for p in prefixes:
                if text.startswith(p):
                    text = text[len(p):].strip()
                    break
            if text:
                logger.debug(f"[qqgal] using inline text as base_text, len={len(text)}")
                return text
        except Exception as e:
            logger.debug("[qqgal] parse inline text failed", exc_info=True)

        # 2) 引用消息（OneBot v11）
        try:
            raw = event.message_obj.raw_message
            if isinstance(raw, dict):
                # 从消息链中找 reply 组件
                reply_id = None
                for seg in raw.get("message", []) or []:
                    if isinstance(seg, dict) and seg.get("type") == "reply":
                        data = seg.get("data", {}) or {}
                        reply_id = data.get("id") or data.get("message_id")
                        break
                if reply_id and event.get_platform_name() == "aiocqhttp":
                    logger.debug(f"[qqgal] detected reply id={reply_id}, try get_msg")
                    # 调 OneBot get_msg
                    try:
                        client = getattr(event, "bot", None)
                        if client is not None:
                            ret = await client.api.call_action("get_msg", message_id=int(reply_id))
                            # ret 结构兼容 OneBot：{"message": [ {type,text...} ] } 或 "message": "..."
                            msg = ret.get("message") if isinstance(ret, dict) else None
                            if isinstance(msg, list):
                                # 拼接纯文本
                                parts = []
                                for seg in msg:
                                    if seg.get("type") == "text":
                                        parts.append(seg.get("data", {}).get("text", ""))
                                txt = "".join(parts).strip()
                                if txt:
                                    logger.debug(f"[qqgal] got quoted text from get_msg, len={len(txt)}")
                                    return txt
                            elif isinstance(msg, str):
                                txt = msg.strip()
                                if txt:
                                    logger.debug(f"[qqgal] got quoted string from get_msg, len={len(txt)}")
                                    return txt
                    except Exception:
                        logger.debug("[qqgal] get_msg failed", exc_info=True)
                        pass
        except Exception:
            logger.debug("[qqgal] parse reply text failed", exc_info=True)
        return ""

    def _letters(self, n: int) -> List[str]:
        base = ord('A')
        return [chr(base + i) for i in range(max(0, n))][:26]

    async def _gen_options(self, event: AstrMessageEvent, base_text: str, option_count: int) -> str:
        cfg = self.cfg()
        provider_id = cfg.get("provider_id", "")
        # 内置系统与风格提示
        system_prompt = "你是一个擅长生成互动小说选项的编剧，输出必须简洁、中文、具代入感。"
        style_hint = "中文表达；强情感；生动但简洁；不含命令/系统语。"

        # 选择供应商：优先ID，否则使用当前会话绑定的供应商
        provider = None
        try:
            if provider_id:
                provider = self.context.get_provider_by_id(provider_id)
        except Exception:
            provider = None
        if provider is None:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        if provider is None:
            return "未找到可用的 LLM 供应商，请在 WebUI 选择或在配置中指定 provider_id。"
        try:
            pid = getattr(provider, "provider_id", None) or getattr(provider, "id", None) or "unknown"
            logger.info(f"[qqgal] generating {option_count} options via provider={pid}")
        except Exception:
            logger.info(f"[qqgal] generating {option_count} options via provider=<unknown>")

        letters = ", ".join(self._letters(option_count))
        first_line = f"请基于这段原文所描述的情境，生成 {option_count} 个极具 GalGame 风格 的下一步选项。"
        tmpl = cfg.get("prompt_template", "")
        prompt = (
            first_line + "\n" + (tmpl.rstrip() + "\n\n" if tmpl else "\n")
            + f"触发选项的对方所说的话：【{base_text if base_text else '（无原文，生成一个遇到重要角色的通用浪漫场景）'}】\n"
            + f"你必须遵循的风格/提示：【{style_hint}】\n"
            + f"需要的选项代号：{letters}。\n"
        )

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt=system_prompt,
                model=cfg.get("model", None)
            )
            # 统一抽取文本
            content = getattr(resp, "text", None) or getattr(resp, "content", None)
            if not content:
                rc = getattr(resp, "result_chain", None)
                if rc and getattr(rc, "chain", None):
                    try:
                        from astrbot.api.message_components import Plain
                    except Exception:
                        Plain = None
                    parts = []
                    for seg in rc.chain:
                        if hasattr(seg, "text"):
                            parts.append(str(seg.text))
                    content = "\n".join(parts)
            if not content:
                content = str(resp)
            content = str(content).strip()
            logger.debug(f"[qqgal] raw llm content len={len(content)}")
            return content
        except Exception as e:
            logger.error(f"调用 LLM 失败: {e}")
            return "LLM 调用失败，请稍后重试。"

    def _normalize_options(self, raw: str, n: int) -> str:
        """规范化 LLM 输出：
        - 优先提取以 大写字母. 开头的行（A./B./C.）。
        - 不足 n 行时，从其余非空行补齐并自动加前缀；超过则截断。
        - 始终输出恰好 n 行。
        """
        lines = [ln.strip() for ln in (raw or "").splitlines()]
        letter_lines = []
        other = []
        for ln in lines:
            if not ln:
                continue
            if len(ln) >= 3 and ln[0].isalpha() and ln[1] == '.' and ln[2] == ' ':
                # 形如 A. 文本
                letter_lines.append(ln)
            else:
                other.append(ln)
        result = []
        # 先取正确格式的
        for ln in letter_lines:
            if len(result) >= n:
                break
            result.append(ln)
        # 不足则从其它行补齐并加前缀
        idx = 0
        letters = self._letters(n)
        while len(result) < n and idx < len(other):
            result.append(f"{letters[len(result)]}. {other[idx]}")
            idx += 1
        # 若仍不足，填充占位
        while len(result) < n:
            result.append(f"{letters[len(result)]}. ……")
        # 只保留 n 行
        return "\n".join(result[:n])

    async def _get_display_and_avatar(self, event: AstrMessageEvent) -> tuple[str, str, str]:
        """优先返回被回复对象（或第一个@对象）的昵称/ID 与头像。

        回退顺序：被回复的人 -> 第一个@的 QQ -> 触发者自身。
        头像采用 qlogo 服务。
        """
        target_id = None
        target_name = None

        try:
            raw = event.message_obj.raw_message
            if isinstance(raw, dict):
                # 1) 被回复对象
                reply_id = None
                for seg in raw.get("message", []) or []:
                    if isinstance(seg, dict) and seg.get("type") == "reply":
                        data = seg.get("data", {}) or {}
                        reply_id = data.get("id") or data.get("message_id")
                        break
                if reply_id and event.get_platform_name() == "aiocqhttp":
                    try:
                        client = getattr(event, "bot", None)
                        if client is not None:
                            ret = await client.api.call_action("get_msg", message_id=int(reply_id))
                            snd = (ret or {}).get("sender", {}) if isinstance(ret, dict) else {}
                            uid = snd.get("user_id") or snd.get("uid") or snd.get("uin")
                            nick = snd.get("card") or snd.get("nickname") or snd.get("nick")
                            # 如果引用的是机器人的消息，则尝试从消息链里找第一个@的人
                            if uid and str(uid) == event.get_self_id():
                                msglist = (ret or {}).get("message") if isinstance(ret, dict) else None
                                if isinstance(msglist, list):
                                    for seg in msglist:
                                        if isinstance(seg, dict) and seg.get("type") == "at":
                                            qq = (seg.get("data", {}) or {}).get("qq")
                                            if qq and qq != "all":
                                                uid = qq
                                                nick = None
                                                break
                            if uid:
                                target_id = str(uid)
                                target_name = str(nick or uid)
                    except Exception:
                        logger.debug("[qqgal] get_msg for avatar failed", exc_info=True)

                # 2) 第一个 @ 对象
                if not target_id:
                    for seg in raw.get("message", []) or []:
                        if isinstance(seg, dict) and seg.get("type") == "at":
                            qq = (seg.get("data", {}) or {}).get("qq")
                            if qq and qq != "all":
                                target_id = str(qq)
                                break

        except Exception:
            logger.debug("[qqgal] parse target for avatar failed", exc_info=True)

        # 3) 触发者自身
        if not target_id:
            target_id = event.get_sender_id()
        if not target_name:
            target_name = event.get_sender_name() or target_id

        avatar_tmpl = str(self.cfg().get("avatar_url_tmpl", "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"))
        avatar = avatar_tmpl.replace("{qq}", target_id)
        display = f"{target_name} ({target_id})"
        return display, avatar, target_id

    def _pick_background(self) -> str:
        base_dir = os.path.dirname(__file__)
        rel = str(self.cfg().get("background_dir", "background"))
        dirp = os.path.join(base_dir, rel)
        try:
            files = [f for f in os.listdir(dirp) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
            if not files:
                return ""
            choice = random.choice(files)
            return os.path.join(dirp, choice)
        except Exception as e:
            logger.debug("[qqgal] pick background failed: %s", e)
            return ""

    def _data_url(self, path: str) -> str:
        try:
            mime, _ = mimetypes.guess_type(path)
            mime = mime or "image/jpeg"
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception as e:
            logger.debug("[qqgal] data_url encode failed: %s", e)
            return ""

    def _get_char_dir(self) -> str:
        base_dir = os.path.dirname(__file__)
        dirp = os.path.join(base_dir, "charactert")
        try:
            os.makedirs(dirp, exist_ok=True)
        except Exception:
            pass
        return dirp

    def _char_file_for(self, qq: str) -> str:
        dirp = self._get_char_dir()
        # 优先使用 png
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            fp = os.path.join(dirp, f"{qq}{ext}")
            if os.path.exists(fp):
                return fp
        return os.path.join(dirp, f"{qq}.png")

    def _char_matte_file_for(self, qq: str) -> str:
        dirp = self._get_char_dir()
        return os.path.join(dirp, f"{qq}-matte.png")

    def _load_character_from_disk(self, qq: str) -> tuple[str, bool]:
        fp = self._char_file_for(qq)
        if not os.path.exists(fp):
            return "", False
        try:
            mime, _ = mimetypes.guess_type(fp)
            mime = mime or "image/png"
            with open(fp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:{mime};base64,{b64}", ("png" in (mime or ""))
        except Exception:
            return "", False

    def _save_data_url_to_disk(self, data_url: str, qq: str, *, save_as_matte: bool = False) -> str:
        try:
            if not data_url.startswith("data:"):
                return ""
            head, b64 = data_url.split(",", 1)
            # 统一转为 PNG 落盘便于后续处理
            raw = base64.b64decode(b64)
            from PIL import Image
            img = Image.open(BytesIO(raw)).convert("RGBA")
            fp = self._char_matte_file_for(qq) if save_as_matte else self._char_file_for(qq)
            with open(fp, "wb") as f:
                buf = BytesIO()
                img.save(buf, format='PNG')
                f.write(buf.getvalue())
            return fp
        except Exception:
            return ""

    def _file_to_data_url(self, fp: str) -> str:
        try:
            with open(fp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:image/png;base64,{b64}"
        except Exception:
            return ""

    def _is_valid_matte_file(self, fp: str) -> bool:
        """判断缓存是否真的包含透明背景。

        旧版即使一个像素都没有抠掉，也会把图片命名为
        ``-matte.png`` 并当成成功缓存。这里至少要求 1% 的像素
        基本透明，同时保留 1% 以上的可见前景。
        """
        try:
            from PIL import Image
            with Image.open(fp) as img:
                alpha = img.convert("RGBA").getchannel("A")
                hist = alpha.histogram()
                total = max(1, alpha.width * alpha.height)
                transparent = sum(hist[:16])
                visible = sum(hist[240:])
                return transparent / total >= 0.01 and visible / total >= 0.01
        except Exception:
            return False

    def _matte_chroma_dataurl_sync(self, data_url: str, chroma: str, tol: int, qq: str) -> tuple[str, bool]:
        """同步抠色实现：CPU 密集，供 to_thread 调用。
        增强项：
        - 从图片边缘自动估计模型实际生成的绿幕色；
        - 采用欧氏距离阈值，以画布边缘连通背景为主，并清理强绿色孔洞；
        - 先腐蚀后高斯模糊，收掉 1~2px 的绿色边缘；
        - 只有确实生成了透明区域才写入 matte 缓存。
        """
        from PIL import Image, ImageFilter
        if not data_url.startswith("data:"):
            return data_url, False
        head, b64 = data_url.split(",", 1)
        base64_bytes = base64.b64decode(b64)
        img = Image.open(BytesIO(base64_bytes)).convert("RGBA")
        hx = chroma.lstrip('#')
        configured_key = tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))

        w, h = img.size
        px = img.load()

        # 生图模型往往不会严格输出 #00FF00，而是偏黄、偏白
        # 或带轻微渐变的绿色。从画布四周采样“绿色占优”像素，
        # 用中位数估计真实背景色，避免少数人物边缘干扰。
        border_size = max(2, min(w, h) // 128)
        border_pixels = []
        for y in range(h):
            for x in range(w):
                if x >= border_size and x < w - border_size and y >= border_size and y < h - border_size:
                    continue
                r, g, b, a = px[x, y]
                if a > 0 and g >= r + 20 and g >= b + 20:
                    border_pixels.append((r, g, b))

        key = configured_key
        border_pixel_count = max(1, 2 * border_size * (w + h - 2 * border_size))
        if len(border_pixels) >= max(32, border_pixel_count // 20):
            rs = sorted(p[0] for p in border_pixels)
            gs = sorted(p[1] for p in border_pixels)
            bs = sorted(p[2] for p in border_pixels)
            mid = len(border_pixels) // 2
            key = (rs[mid], gs[mid], bs[mid])
            logger.info(
                "[qqgal-生图] 自动识别绿幕色: rgb=%s（配置值=%s，边缘样本=%d）",
                str(key),
                str(configured_key),
                len(border_pixels),
            )
        else:
            logger.info("[qqgal-生图] 边缘绿色样本不足，使用配置抠色: rgb=%s", str(configured_key))

        tolerance = max(0, min(255, int(tol)))
        thr2 = tolerance * tolerance
        total = w * h
        candidate = bytearray(total)
        strong_chroma = bytearray(total)
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                dr = r - key[0]
                dg = g - key[1]
                db = b - key[2]
                if a > 0 and (dr*dr + dg*dg + db*db) <= thr2:
                    index = y * w + x
                    candidate[index] = 1
                    # 高饱和绿色可能被发丝等前景包围，与画布边缘
                    # 不连通。将这类强绿色单独标记，后续一并清除孔洞。
                    if g >= r + 35 and g >= b + 35:
                        strong_chroma[index] = 1

        # 先从四条边向内扩展绿幕，尽量避免将人物内部颜色当成
        # 背景；后面再仅对高饱和强绿色补清被发丝包围的背景孔洞。
        background = bytearray(total)
        queue = deque()

        def enqueue(index: int):
            if candidate[index] and not background[index]:
                background[index] = 1
                queue.append(index)

        for x in range(w):
            enqueue(x)
            enqueue((h - 1) * w + x)
        for y in range(h):
            enqueue(y * w)
            enqueue(y * w + w - 1)

        while queue:
            index = queue.popleft()
            x = index % w
            if x > 0:
                enqueue(index - 1)
            if x + 1 < w:
                enqueue(index + 1)
            if index >= w:
                enqueue(index - w)
            if index + w < total:
                enqueue(index + w)

        mask_bytes = bytearray(b'\xff') * total
        removed = 0
        for index, is_background in enumerate(background):
            if is_background or strong_chroma[index]:
                mask_bytes[index] = 0
                removed += 1
        mask = Image.frombytes('L', (w, h), bytes(mask_bytes))

        removed_ratio = removed / max(1, total)
        if removed_ratio < 0.01 or removed_ratio > 0.99:
            logger.error(
                "[qqgal-生图] 抠色结果异常，不写入缓存: 透明区域比例=%.2f%%, key=%s, tolerance=%d",
                removed_ratio * 100,
                str(key),
                tolerance,
            )
            return data_url, False

        # 轻度腐蚀收边，去掉 1~2 像素绿边
        try:
            mask = mask.filter(ImageFilter.MinFilter(3))
        except Exception:
            pass
        # 轻度羽化
        mask = mask.filter(ImageFilter.GaussianBlur(1.2))
        r, g, b, original_alpha = img.split()
        # 保留上游图片本来就有的透明度。
        alpha = Image.new('L', (w, h), 0)
        alpha_px = alpha.load()
        mask_px = mask.load()
        original_alpha_px = original_alpha.load()
        for y in range(h):
            for x in range(w):
                alpha_px[x, y] = min(mask_px[x, y], original_alpha_px[x, y])
        img = Image.merge('RGBA', (r, g, b, alpha))

        buf = BytesIO()
        img.save(buf, format='PNG')
        b64png = base64.b64encode(buf.getvalue()).decode('ascii')
        final_url = f"data:image/png;base64,{b64png}"
        try:
            self._save_data_url_to_disk(final_url, qq, save_as_matte=True)
        except Exception:
            pass
        logger.info(
            "[qqgal-生图] 抠色完成: 透明区域比例=%.2f%%, key=%s, tolerance=%d",
            removed_ratio * 100,
            str(key),
            tolerance,
        )
        return final_url, True

    async def _matte_chroma_dataurl(self, data_url: str, chroma: str, tol: int, qq: str) -> tuple[str, bool]:
        """异步封装：在独立线程执行抠色，避免阻塞事件循环。"""
        try:
            return await asyncio.to_thread(self._matte_chroma_dataurl_sync, data_url, chroma, tol, qq)
        except Exception:
            logger.error("[qqgal-生图] 抠色处理失败(缓存/回退路径)", exc_info=True)
            return data_url, ("data:image/png" in data_url)

    def _standardize_character_canvas_sync(self, data_url: str, size: int, width_ratio: float, bottom_pad: int, qq: str) -> str:
        """将抠好的人物立绘标准化到 size×size 透明画布中：
        - 等比放大到“左右对齐”（宽度=画布宽度），
        - 基于轮廓质心做水平居中，避免人物偏一侧；
        - 底部对齐（人物底边贴近画布底边，允许上方溢出被裁切），
        以确保不同原图比例得到一致的最终合成尺寸。
        返回 data-url，并覆盖到 qq-matte.png。
        """
        from PIL import Image
        if not data_url.startswith("data:"):
            return data_url
        head, b64 = data_url.split(",", 1)
        img = Image.open(BytesIO(base64.b64decode(b64))).convert("RGBA")
        # 取非透明 bbox
        alpha = img.split()[3]
        bbox = alpha.getbbox()
        if not bbox:
            return data_url
        crop = img.crop(bbox)
        alpha_crop = alpha.crop(bbox)
        # 从底部往上等比放大：尽可能大但不裁切（contain），
        # 当任意边触达画布边框时停止放大。
        scale = min(size / max(1, crop.width), size / max(1, crop.height))
        new_w = max(1, int(crop.width * scale))
        new_h = max(1, int(crop.height * scale))
        crop = crop.resize((new_w, new_h), Image.LANCZOS)
        alpha_crop = alpha_crop.resize((new_w, new_h), Image.LANCZOS)
        # 计算轮廓质心，按质心居中
        try:
            # 质心 x：sum(x*mask)/sum(mask)
            sum_w = 0
            sum_xw = 0
            px = alpha_crop.load()
            for y in range(new_h):
                row_sum = 0
                row_xw = 0
                for x in range(new_w):
                    wv = px[x, y]
                    if wv > 0:
                        sum_w += wv
                        sum_xw += wv * x
            cx = (sum_xw / sum_w) if sum_w > 0 else (new_w / 2)
        except Exception:
            cx = new_w / 2
        # 粘贴到标准画布
        canvas = Image.new('RGBA', (size, size), (0,0,0,0))
        desired_cx = size / 2
        shift = int(round(desired_cx - cx))
        x = (size - new_w) // 2 + shift
        # 约束不越界
        if x < 0:
            x = 0
        if x > size - new_w:
            x = size - new_w
        # 底部对齐，不裁切
        y = max(0, size - new_h - int(bottom_pad or 0))
        canvas.paste(crop, (x, y), crop)
        buf = BytesIO()
        canvas.save(buf, format='PNG')
        url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
        try:
            self._save_data_url_to_disk(url, qq, save_as_matte=True)
        except Exception:
            pass
        return url

    async def _download_to_b64(self, url: str) -> tuple[str, str]:
        """下载图片为 base64 与 mime。"""
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        return "", ""
                    data = await resp.read()
                    ctype = resp.headers.get("Content-Type", "image/jpeg")
                    return base64.b64encode(data).decode("ascii"), ctype
        except Exception:
            return "", ""

    async def _generate_character_image(self, name: str, avatar_url: str, qq: str, force_refresh: bool = False) -> tuple[str, bool]:
        """调用 Gemini 生成半身像，返回 data-url 与是否透明 PNG。失败返回("", False)。"""
        cfg = self.cfg()
        if not bool(cfg.get("enable_character", False)):
            logger.info("[qqgal-生图] 未启用人物生图，跳过。")
            return "", False
        if not force_refresh:
            # 优先读取 matte 文件
            matte_fp = self._char_matte_file_for(qq)
            if os.path.exists(matte_fp):
                if self._is_valid_matte_file(matte_fp):
                    logger.info("[qqgal-生图] 命中抠图缓存，直接使用: %s", matte_fp)
                    return self._file_to_data_url(matte_fp), True
                logger.warning("[qqgal-生图] 抠图缓存没有有效透明通道，忽略并重新生成: %s", matte_fp)
            cached, is_png = self._load_character_from_disk(qq)
            if cached:
                logger.info("[qqgal-生图] 读取本地缓存立绘成功(未抠)，qq=%s，透明PNG=%s，开始补抠。", qq, str(is_png))
                tol = int(cfg.get("chroma_tolerance", 80))
                chroma = str(cfg.get("chroma_bg_color", "#00FF00"))
                processed, is_png2 = await self._matte_chroma_dataurl(cached, chroma, tol, qq)
                if is_png2:
                    return processed, True
                logger.error("[qqgal-生图] 本地原图补抠失败，不将带绿幕图片加入合成。")
                return "", False
        keys_val = cfg.get("gemini_api_keys", [])
        api_keys = []
        if isinstance(keys_val, list):
            api_keys = [str(k).strip() for k in keys_val if isinstance(k, (str,)) and str(k).strip()]
        elif isinstance(keys_val, str):
            api_keys = [k.strip() for k in keys_val.split(",") if k.strip()]
        if not api_keys:
            logger.error("[qqgal-生图] 未配置 Gemini API Key，无法生成人物。")
            return "", False
        base_url = str(cfg.get("gemini_base_url", "")).strip() or "https://generativelanguage.googleapis.com"
        model = str(cfg.get("gemini_model", "gemini-3.1-flash-image")).strip()
        if not model:
            logger.error("[qqgal-生图] 未配置 Gemini 生图模型名。")
            return "", False
        prompt_tmpl = str(cfg.get("character_prompt", "以 {name} 的头像为参考，生成一位二次元风格的完整半身像角色，面向正前方，透明背景，立绘适合 Galgame 对话立绘使用。"))
        # 为不透明背景做准备：强制一个易抠图的纯色底
        chroma = str(cfg.get("chroma_bg_color", "#00FF00"))
        prompt = (prompt_tmpl.replace("{name}", name) + f"\n背景：{chroma} 纯色背景，人物完整半身像，无遮挡。")

        logger.info("[qqgal-生图] 开始下载头像用于参考，qq=%s，url=%s", qq, avatar_url)
        b64_avatar, mime_avatar = await self._download_to_b64(avatar_url)
        if not b64_avatar:
            logger.error("[qqgal-生图] 下载头像失败，放弃此次生图。")
            return "", False

        # 始终使用 WebUI 中配置的 gemini_model。旧版曾在一个隐藏的
        # legacy_image_endpoint 中写死 Gemini 2.0，导致用户修改模型名无效。
        model_path = model
        if model_path.startswith("models/"):
            model_path = model_path[len("models/"):]
        if model_path.endswith(":generateContent"):
            model_path = model_path[:-len(":generateContent")]

        api_root = base_url.rstrip("/")
        if api_root.endswith(("/v1", "/v1beta")):
            endpoint = f"{api_root}/models/{model_path}:generateContent"
        else:
            endpoint = f"{api_root}/v1beta/models/{model_path}:generateContent"

        # Gemini 原生 generateContent 图文输入：头像作为参考图，
        # responseModalities 明确要求返回图片。
        req = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": mime_avatar or "image/jpeg",
                                "data": b64_avatar,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "temperature": 0.8,
                "topP": 0.95,
                "topK": 40,
                "maxOutputTokens": 1024,
            },
        }

        for idx, key in enumerate(api_keys):
            try:
                logger.info("[qqgal-生图] 调用 Gemini，尝试第 %d 个 Key，endpoint=%s，期望输出=PNG", idx + 1, endpoint)
                async with aiohttp.ClientSession() as sess:
                    async with sess.post(f"{endpoint}?key={key}", json=req, timeout=60) as resp:
                        if resp.status != 200:
                            try:
                                err_text = await resp.text()
                            except Exception:
                                err_text = "<无返回文本>"
                            logger.error("[qqgal-生图] 接口返回非 200（%d）：%s", resp.status, err_text[:300])
                            continue
                        data = await resp.json()
                        logger.info("[qqgal-生图] 接口请求成功，开始解析返回数据。")
                        # 兼容多种返回结构，尽力找到内联图片数据（inline_data 或 inlineData）
                        def find_inline(d: Any):
                            if isinstance(d, dict):
                                # both snake_case and camelCase
                                if (
                                    ("inline_data" in d and isinstance(d["inline_data"], dict) and "data" in d["inline_data"]) or
                                    ("inlineData" in d and isinstance(d["inlineData"], dict) and "data" in d["inlineData"])  
                                ):
                                    return d.get("inline_data") or d.get("inlineData")
                                for v in d.values():
                                    r = find_inline(v)
                                    if r:
                                        return r
                            elif isinstance(d, list):
                                for it in d:
                                    r = find_inline(it)
                                    if r:
                                        return r
                            return None
                        inline = find_inline(data)
                        if inline and inline.get("data"):
                            mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
                            b64 = inline.get("data")
                            data_url = f"data:{mime};base64,{b64}"
                            logger.info("[qqgal-生图] 解析图片成功，mime=%s，长度=%d 字符，准备抠色并写入缓存。", mime, len(b64))
                            # 1) 先保存原始到 qq.png
                            raw_fp = self._save_data_url_to_disk(data_url, qq, save_as_matte=False)
                            # 2) 抠色到 qq-matte.png 并删除 qq.png
                            tol = int(cfg.get("chroma_tolerance", 80))
                            chroma = str(cfg.get("chroma_bg_color", "#00FF00"))
                            matte_url, matte_ok = await self._matte_chroma_dataurl(self._file_to_data_url(raw_fp), chroma, tol, qq)
                            if not matte_ok:
                                logger.error("[qqgal-生图] 新生成立绘抠色失败，保留原图供调整参数后重试。")
                                return "", False
                            # 立绘标准化：固定到方形画布，确保位置与大小一致
                            std_size = int(cfg.get('character_canvas_size', 1024))
                            target_ratio = float(cfg.get('character_target_width_ratio', 0.55))
                            matte_url = await asyncio.to_thread(
                                self._standardize_character_canvas_sync,
                                matte_url,
                                std_size,
                                target_ratio,
                                0,
                                qq,
                            )
                            try:
                                os.remove(raw_fp)
                            except Exception:
                                pass
                            return matte_url, True
            except Exception:
                logger.error("[qqgal-生图] 调用 Gemini 发生异常，尝试下一个 Key。", exc_info=True)
                continue
        logger.error("[qqgal-生图] 所有 Key 均尝试失败，放弃此次生图。")
        return "", False

    async def _render_image(self, event: AstrMessageEvent, quote: str, options: List[str]) -> str:
        cfg = self.cfg()
        width = int(cfg.get("canvas_width", 1280))
        height = int(cfg.get("canvas_height", 720))
        bg = self._pick_background()
        name, avatar, target_id = await self._get_display_and_avatar(event)
        # 嵌入为 data URL，避免 file:// 在某些环境不可读/中文路径问题
        bg_url = self._data_url(bg) if bg else ""
        # 生成半身像（可选）
        char_url, char_is_png = await self._generate_character_image(name, avatar, qq=target_id, force_refresh=False)
        if char_url:
            logger.info("[qqgal-生图] 立绘生成/读取成功，准备合成，PNG=%s", str(char_is_png))
        else:
            logger.info("[qqgal-生图] 未添加立绘（未启用/失败/无Key/缓存缺失）。")

        # 选项纵向位置（保持既有结构）
        opt1_top = int(height * 0.20)
        opt2_top = int(height * 0.34)
        opt3_top = int(height * 0.48)
        opt4_top = int(height * 0.62)

        # 引用框宽度（用于与头像/名字关联），以及引用块顶端位置
        quote_w = int(width * 0.86)
        quote_top = max(opt3_top + 110, int(height * 0.68))
        # 仅用于引用区域的延伸毛玻璃（从引用块顶端到底部），覆盖整幅画面的下半部分
        glass_left = 24
        glass_w = max(0, width - 48)
        glass_top = quote_top
        glass_h = max(120, height - glass_top)

        # 对外部/用户内容进行 HTML 转义，避免注入
        safe_name = html_lib.escape(name)
        safe_quote = html_lib.escape(quote)
        safe_options = [html_lib.escape(opt) for opt in options]

        # 构建 HTML 模板
        html_doc = f"""
<html>
<head>
<meta charset='utf-8'/>
<style>
  body {{ margin:0; width:{width}px; height:{height}px; font-family: 'Microsoft Yahei', sans-serif; }}
  .root {{ position:relative; width:{width}px; height:{height}px; background:#000; overflow:hidden; }}
  /* 两层背景：底层模糊铺满，顶层等比完整展示，保证任意比例都好看 */
  .bg-blur {{ position:absolute; inset:0; background-image:url('{bg_url}'); background-size:cover; background-position:center; filter:blur(18px) brightness(0.7); transform:scale(1.06); z-index:0; }}
  .bg-main {{ position:absolute; inset:0; background-image:url('{bg_url}'); background-repeat:no-repeat; background-size:contain; background-position:center; z-index:0; }}
  .topbar {{ position:absolute; left:24px; top:18px; color:#fff; font-weight:700; letter-spacing:1px; text-shadow:0 2px 6px rgba(0,0,0,.6); }}
  :root {{ --quote-width: {quote_w}px; }}
  /* 人物立绘：底部居中，宽度按比例缩放 */
  .char {{ position:absolute; left:calc(50% + {int(cfg.get('character_x_offset', 0))}px); transform:translateX(-50%); bottom:{int(cfg.get('character_bottom_offset', 0))}px; width:{int(width*float(cfg.get('character_scale', 0.42)))}px; height:auto; object-fit:contain; {'' if char_is_png else 'mix-blend-mode: multiply;'} filter: drop-shadow(0 8px 24px rgba(0,0,0,.45)); opacity:{1.0 if char_url else 0}; z-index: 1; pointer-events:none; }}
  /* 引用内容容器：自身不加毛玻璃，由下方 .glass 提供延伸到底部的模糊背景 */
  .quote {{ position:absolute; left:50%; transform:translateX(-50%); top:{quote_top}px; width:var(--quote-width); padding:18px 22px 22px 22px; color:#fff; font-size:28px; font-weight:800; line-height:1.5; border-radius:16px; background:transparent; text-align:center; z-index:3; }}
  .glass {{ position:absolute; left:{glass_left}px; top:{glass_top}px; width:{glass_w}px; height:{glass_h}px; background:rgba(0,0,0,.25); backdrop-filter: blur(10px); border-radius:18px; box-shadow:0 10px 30px rgba(0,0,0,.35); z-index:2; }}
  .q-avatar {{ position:absolute; left:16px; top:16px; width:56px; height:56px; border-radius:50%; border:2px solid rgba(255,255,255,.8); background-image:url('{avatar}'); background-size:cover; background-position:center; box-shadow:0 4px 12px rgba(0,0,0,.4); z-index:3; }}
  .q-user {{ position:absolute; left:88px; top:22px; font-size:22px; font-weight:800; color:#fff; text-shadow:0 2px 6px rgba(0,0,0,.6); z-index:3; }}
  .q-text {{ margin-top:88px; font-size:32px; font-weight:900; color:#fff; text-align:center; line-height:1.6; z-index:3; position:relative; }}
  .option {{ position:absolute; left:50%; transform:translateX(-50%); width:{int(width*0.7)}px; padding:14px 18px; background:rgba(0,0,0,.55); color:#f0f0f0; border-radius:28px; text-align:center; font-size:26px; font-weight:800; letter-spacing:1px; box-shadow:0 8px 20px rgba(0,0,0,.35); border:1px solid rgba(255,255,255,.15); z-index:3; }}
  /* 将选项整体上移，集中在画面上 2/5 区域附近 */
  .opt1 {{ top:{opt1_top}px; }}
  .opt2 {{ top:{opt2_top}px; }}
  .opt3 {{ top:{opt3_top}px; }}
  .opt4 {{ top:{opt4_top}px; }}
</style>
</head>
<body>
  <div class='root'>
    <div class='bg-blur'></div>
    <div class='bg-main'></div>
    <div class='topbar'>CHAPTER</div>
    <img class='char' src='{char_url}' />
    <div class='glass'></div>
    <div class='quote'>
      <div class='q-avatar'></div>
      <div class='q-user'>{safe_name}</div>
      <div class='q-text'>{safe_quote}</div>
    </div>
    {''.join([f"<div class='option opt{i+1}'>"+opt+"</div>" for i,opt in enumerate(safe_options)])}
  </div>
</body>
</html>
"""
        # 输出图片质量（仅 jpeg 生效）
        quality = int(cfg.get("image_quality", 85))
        if quality < 10:
            quality = 10
        if quality > 100:
            quality = 100
        options_dict = {"type": "jpeg", "quality": quality}
        url = await self.html_render(html_doc, data={}, options=options_dict)
        return url

    def _parse_count_from_text(self, text: str, default_n: int, min_n: int, max_n: int) -> int:
        try:
            nums = []
            cur = ""
            for ch in text:
                if ch.isdigit():
                    cur += ch
                else:
                    if cur:
                        nums.append(int(cur))
                        cur = ""
            if cur:
                nums.append(int(cur))
            if nums:
                n = nums[-1]
                return max(min_n, min(max_n, n))
        except Exception:
            pass
        return max(min_n, min(max_n, default_n))

    @filter.command("选项", alias={"gal", "gal选项"})
    async def make_gal_options(self, event: AstrMessageEvent):
        """引用或跟随文本，生成 GalGame 风格选项。数量可选，默认 3。"""
        try:
            # 标记本事件已由主指令处理，供 fallback 去重
            try:
                event.set_extra("qqgal_handled", True)
            except Exception:
                pass
            cfg = self.cfg()
            default_n = int(cfg.get("option_count", 3))
            # 从文本中解析数量（最后一个整数）；无则用默认；限制 1~26
            n = self._parse_count_from_text(event.message_str or "", default_n, 1, 26)
            logger.debug(f"[qqgal] parsed option count n={n}")

            base_text = await self._extract_quoted_text(event)
            sep = cfg.get("message_separator", "-------------------------")
            title = cfg.get("title", "🎮 GalGame 选项")
            show_quote = bool(cfg.get("show_quote", True))

            options_raw = await self._gen_options(event, base_text, n)
            options_text = self._normalize_options(options_raw, n)
            logger.debug(f"[qqgal] normalized options:\n{options_text}")

            if bool(cfg.get("render_image", False)):
                options_list = [ln.strip() for ln in options_text.splitlines() if ln.strip()]
                img_url = await self._render_image(event, base_text or "（无原文）", options_list)
                yield event.image_result(img_url)
            else:
                lines = [title, sep]
                if show_quote and base_text:
                    lines.append(f"📝 原文：{base_text}")
                    lines.append(sep)
                lines.append(options_text)
                yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"生成选项失败: {e}")
            yield event.plain_result("生成选项失败，请稍后重试。")

    @filter.command("刷新立绘")
    async def refresh_character(self, event: AstrMessageEvent):
        try:
            # 解析目标：若引用他人，则仅允许自己=被引用人
            _, avatar_url, target_id = await self._get_display_and_avatar(event)
            sender_id = event.get_sender_id()
            if target_id != sender_id:
                yield event.plain_result("仅可刷新自己立绘")
                return
            name = event.get_sender_name() or sender_id
            # 强制刷新并缓存
            data_url, _ = await self._generate_character_image(name, avatar_url, qq=target_id, force_refresh=True)
            if data_url:
                yield event.plain_result("已刷新你的立绘~")
            else:
                yield event.plain_result("刷新失败，请检查 Key/网络后再试。")
        except Exception as e:
            logger.error(f"刷新立绘失败: {e}")
            yield event.plain_result("刷新失败，请稍后重试。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _fallback_any(self, event: AstrMessageEvent):
        """兼容某些平台在消息前插入 reply 等组件导致命令未命中的情况。
        当文本中以 /选项、选项、/gal、gal 起始时，触发与命令相同的逻辑。
        """
        try:
            # 若主指令已处理，直接跳过，防止重复发送
            try:
                if event.get_extra("qqgal_handled"):
                    return
            except Exception:
                pass
            text = (event.message_str or "").strip()
            raw = text.lstrip('*').lstrip()
            prefixes = ("/选项", "选项", "/gal", "gal")
            if not any(raw.startswith(p) for p in prefixes):
                return
            # 调用与指令一致的处理（make_gal_options 为 async generator，需要逐条转发其结果）
            logger.debug("[qqgal] fallback trigger matched, dispatch to make_gal_options")
            async for result in self.make_gal_options(event):
                yield result
            # 阻断默认 LLM 回复
            event.stop_event()
        except Exception:
            logger.error("[qqgal] fallback handler failed", exc_info=True)

    async def terminate(self):
        pass
