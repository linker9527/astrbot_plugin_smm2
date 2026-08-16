# -*- coding: utf-8 -*-
"""
AstrBot Plugin: SMM2 (Super Mario Maker 2)
关卡查询 / 玩家查询 / 随机抽图 / bcd 下载 / 关卡渲染 / LLM Tool + OCR 图片识别
v1.2.1
"""
import asyncio
import gzip
import os
import re
import subprocess
import zipfile
from typing import Optional

import aiohttp

from astrbot.api.star import Star, Context, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.message_components import Plain, Image, File
from astrbot.api.all import MessageChain, llm_tool
from astrbot.api import logger


# ============ API 常量 ============

API_BASE = "https://tgrcode.com/mm2"
LEVEL_INFO = f"{API_BASE}/level_info"
THUMB_URL = f"{API_BASE}/level_entire_thumbnail"
LEVEL_DATA = f"{API_BASE}/level_data"
USER_INFO = f"{API_BASE}/user_info"
GET_POSTED = f"{API_BASE}/get_posted"
GET_LIKED = f"{API_BASE}/get_liked"
GET_PLAYED = f"{API_BASE}/get_played"
GET_FIRST_CLEARED = f"{API_BASE}/get_first_cleared"
GET_WORLD_RECORD = f"{API_BASE}/get_world_record"
SEARCH_ENDLESS = f"{API_BASE}/search_endless_mode"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://tgrcode.com/",
    "Accept": "application/json, text/plain, */*",
}

MAX_RETRIES = 5
RETRY_DELAY = 2

# toost 渲染器路径（插件目录下）
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
TOOST_EXE = os.path.join(_PLUGIN_DIR, "toost", "bin", "toost.exe")
TOOST_WORK = os.path.join(_PLUGIN_DIR, "toost")
TOOST_RENDER = os.path.join(TOOST_WORK, "render")

TOOST_DOWNLOAD_URLS = [
    "https://www.now61.cn/f/kVz6Ty/toost_windows.zip",
    "https://github.com/TheGreatRambler/toost/"
    "releases/latest/download/toost_windows.zip",
]
TOOST_ZIP_PATH = os.path.join(_PLUGIN_DIR, "_tmp", "toost_windows.zip")
TOOST_DOWNLOADED = False


async def _ensure_toost():
    """如果 toost 不存在，依次尝试网盘直链和 GitHub 下载"""
    global TOOST_DOWNLOADED
    if TOOST_DOWNLOADED:
        return True
    if os.path.exists(TOOST_EXE):
        TOOST_DOWNLOADED = True
        return True

    os.makedirs(os.path.join(_PLUGIN_DIR, "_tmp"), exist_ok=True)

    for url in TOOST_DOWNLOAD_URLS:
        if not url:
            continue
        source = "网盘" if "github.com" not in url else "GitHub"
        logger.info(f"[SMM2] 检测到 toost 不存在，正在从{source}下载...")
        ok = await _download_toost(url)
        if ok:
            TOOST_DOWNLOADED = True
            logger.info(f"[SMM2] toost 从{source}下载完成")
            return True
        logger.warning(f"[SMM2] 从{source}下载失败，尝试下一个源...")

    logger.error("[SMM2] 所有下载源均失败")
    return False


async def _download_toost(url):
    """从指定 URL 下载 toost 并解压，返回是否成功"""
    try:
        if os.path.exists(TOOST_ZIP_PATH):
            os.unlink(TOOST_ZIP_PATH)

        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=180, ssl=False) as r:
                if r.status != 200:
                    logger.error(f"[SMM2] 下载 toost 失败 HTTP {r.status}")
                    return False
                total = int(r.headers.get("Content-Length", "0"))
                downloaded = 0
                bar_len = 30
                with open(TOOST_ZIP_PATH, "wb") as f:
                    async for chunk in r.content.iter_chunked(8192):
                        downloaded += len(chunk)
                        f.write(chunk)
                        if total > 0:
                            pct = downloaded / total
                            filled = int(bar_len * pct)
                            bar = "█" * filled + "░" * (bar_len - filled)
                            kb = downloaded / 1024
                            kb_total = total / 1024
                            logger.info(
                                f"[SMM2] toost 下载 [{bar}] {pct:.0%} "
                                f"({kb:.0f}/{kb_total:.0f} KB)"
                            )

        with zipfile.ZipFile(TOOST_ZIP_PATH, "r") as z:
            z.extractall(os.path.join(_PLUGIN_DIR, "toost"))
        try:
            os.unlink(TOOST_ZIP_PATH)
        except Exception:
            pass

        if os.path.exists(TOOST_EXE):
            return True
        logger.error("[SMM2] toost 解压后 exe 未找到")
        return False
    except Exception as e:
        logger.error(f"[SMM2] 下载 toost 失败: {e}")
        return False

# SMM2 ID 字符集：0-9 + A-Y，排除 I、O、Z
# OCR 纠错候选：O→0, I→1或L, Z→2
_OCR_FIX_VARIANTS = [
    {"O": "0", "I": "L", "Z": "2", "o": "0", "i": "L", "z": "2"},
    {"O": "0", "I": "1", "Z": "2", "o": "0", "i": "1", "z": "2"},
    {"O": "0", "I": "7", "Z": "2", "o": "0", "i": "7", "z": "2"},
]


def fix_smm2_id(pure_id: str, variant: int = 0) -> str:
    """纠正 OCR 识别的 ID，将 I/O/Z 替换为合法字符。variant 选择纠错方案"""
    if variant >= len(_OCR_FIX_VARIANTS):
        variant = 0
    fix = _OCR_FIX_VARIANTS[variant]
    return "".join(fix.get(c, c) for c in pure_id)


def generate_correction_candidates(pure_id: str):
    """生成最多3种纠错候选 ID"""
    seen = set()
    result = []
    for i in range(len(_OCR_FIX_VARIANTS)):
        candidate = fix_smm2_id(pure_id, i)
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
        if len(result) >= 3:
            break
    return result


async def try_query_level_with_corrections(session, pure_id):
    """尝试用最多3种纠错方案查询关卡，返回 (level, used_id) 或 (None, None)"""
    candidates = generate_correction_candidates(pure_id)
    for candidate in candidates:
        level = await fetch_level(session, candidate)
        if level:
            return level, candidate
    return None, candidates[0] if candidates else None


def parse_id(raw: str) -> Optional[str]:
    raw = raw.strip()
    m = re.search(r"[0-9A-Za-z]{3}-[0-9A-Za-z]{3}-[0-9A-Za-z]{3}", raw)
    if m:
        return m.group(0).upper().replace("-", "")
    m = re.search(r"[0-9A-Za-z]{9}", raw)
    if m:
        return m.group(0).upper()
    return None


async def api_get(session: aiohttp.ClientSession, url: str, *, as_json: bool = True):
    last_err = ""
    for i in range(MAX_RETRIES):
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 429:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                if r.status != 200:
                    last_err = await r.text()
                    return {"_status": r.status, "_body": last_err}
                if as_json:
                    return await r.json()
                return await r.read()
        except asyncio.TimeoutError:
            await asyncio.sleep(RETRY_DELAY)
            continue
        except Exception as e:
            last_err = str(e)
            await asyncio.sleep(RETRY_DELAY)
            continue
    return {"_status": 429, "_body": "max retries exceeded"}


async def fetch_level(session, pure_id):
    data = await api_get(session, f"{LEVEL_INFO}/{pure_id}")
    if data and isinstance(data, dict) and "_status" not in data:
        return data
    return None


async def fetch_player(session, mid):
    user_data = await api_get(session, f"{USER_INFO}/{mid}")
    lists = {"posted": [], "liked": [], "played": [], "first_clear": [], "wr": []}
    if user_data and isinstance(user_data, dict) and "_status" not in user_data:
        for key, endpoint in [("posted", GET_POSTED), ("liked", GET_LIKED),
                               ("played", GET_PLAYED), ("first_clear", GET_FIRST_CLEARED),
                               ("wr", GET_WORLD_RECORD)]:
            try:
                d = await api_get(session, f"{endpoint}/{mid}")
                if d and isinstance(d, dict) and "_status" not in d:
                    lists[key] = d.get("courses", []) or []
            except Exception:
                pass
    return user_data, lists


async def download_bcd(session, pure_id, cache_dir):
    raw = await api_get(session, f"{LEVEL_DATA}/{pure_id}", as_json=False)
    if not raw or isinstance(raw, dict):
        return None
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    os.makedirs(cache_dir, exist_ok=True)
    fpath = os.path.join(cache_dir, f"{pure_id}.bcd")
    with open(fpath, "wb") as f:
        f.write(raw)
    return fpath


def fmt_courses(courses, limit=5):
    if not courses:
        return "暂无数据"
    parts = []
    for c in courses[:limit]:
        cid = c.get("course_id") or "?"
        cname = c.get("name") or "无名称"
        cl = c.get("likes") or 0
        cr = c.get("clear_rate_pretty") or c.get("clear_rate") or "无"
        parts.append(f"ID：{cid} | 名称：{cname} | 点赞：{cl} | 通关率：{cr}")
    if len(courses) > limit:
        parts.append(f"... 共 {len(courses)} 个")
    return "\n".join(parts)


def _send_text(event, text):
    return event.send(MessageChain([Plain(text)]))


# ============ 插件主类 ============

@register(
    "astrbot_plugin_smm2",
    "linker9527",
    "超级马力欧制造2关卡/玩家查询、随机抽图、bcd下载、关卡渲染、LLM Tool+OCR图片识别",
    "1.2.1",
    "https://github.com/linker9527/astrbot_plugin_smm2",
)
class Smm2Plugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._llm_hint = ""
        if self.config:
            self._llm_hint = self.config.get("llm_hint") or ""
        self._tmp_dir = os.path.join(_PLUGIN_DIR, "_tmp")
        os.makedirs(self._tmp_dir, exist_ok=True)
        os.makedirs(TOOST_RENDER, exist_ok=True)

    # ==================== 图片拦截器 ====================

    @filter.regex(r".*", priority=5)
    async def on_image_message(self, event: AstrMessageEvent, match_obj=None):
        """拦截含图片的消息，从图片OCR提取关卡ID，直接渲染返回"""
        # OCR开关检查
        if not (self.config and self.config.get("enable_ocr", False)):
            return  # OCR关闭，不拦截图片

        message_obj = event.message_obj
        img_comp = None
        if message_obj and hasattr(message_obj, "message") and message_obj.message:
            for comp in message_obj.message:
                if isinstance(comp, Image):
                    img_comp = comp
                    break
        if not img_comp:
            return  # 没图片，不拦截

        # 优先从文字提取ID，纯图片再走OCR
        msg_str = event.get_message_str() or ""
        pure_id = parse_id(msg_str) or ""

        if not pure_id:
            pure_id = await self._ocr_extract_id(img_comp) or ""
            if not pure_id:
                return  # OCR失败，让LLM处理

        pure_id = pure_id.upper()
        logger.info(f"[SMM2] 图片拦截到ID: {pure_id}")

        quality = "high"
        if self.config:
            quality = (self.config.get("image_quality") or "high").strip().lower()

        async with aiohttp.ClientSession() as s:
            level, used_id = await try_query_level_with_corrections(s, pure_id)
            if not level:
                return

            pure_id = used_id
            body = self._fmt_level(pure_id, level)

            if quality == "high" and await _ensure_toost():
                images = await self._do_render(event, pure_id)
                if images:
                    await event.send(MessageChain([Plain(body)]))
                    for img_path in images:
                        try:
                            await event.send(MessageChain([Image(file=img_path)]))
                        except Exception as e:
                            logger.error(f"[SMM2] 图片发送失败: {e}")
                    for fp in images:
                        try: os.unlink(fp)
                        except: pass
                    event.stop_event()
                    return
            img_url = f"{THUMB_URL}/{pure_id}"
            await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
            event.stop_event()

    async def _ocr_extract_id(self, img_comp) -> str:
        """下载图片后用支持图片的provider做OCR，提取关卡ID"""
        # 获取图片URL
        img_url = getattr(img_comp, "url", "") or getattr(img_comp, "file", "")
        if not img_url:
            return ""

        # 统一下载图片，转成 base64 data URL
        try:
            if img_url.startswith("http"):
                async with aiohttp.ClientSession() as s:
                    async with s.get(img_url, timeout=30) as r:
                        raw = await r.read()
            elif os.path.exists(img_url):
                with open(img_url, "rb") as f:
                    raw = f.read()
            else:
                logger.error(f"[SMM2] 图片URL无法访问: {img_url[:100]}")
                return ""
        except Exception as e:
            logger.error(f"[SMM2] 下载图片失败: {e}")
            return ""

        import base64 as _b64
        b64 = _b64.b64encode(raw).decode()
        data_url = f"data:image/jpeg;base64,{b64}"

        # 找支持图片的provider
        vision_provider = None
        for prov in self.context.get_all_providers():
            modalities = prov.provider_config.get("modalities", [])
            if modalities == [] or "image" in modalities:
                vision_provider = prov
                break
        if not vision_provider:
            logger.warning("[SMM2] 未找到支持图片的provider，OCR跳过")
            return ""

        # 调用provider做OCR
        try:
            resp = await vision_provider.text_chat(
                prompt="这是一张游戏截图。请提取图片中的关卡ID，格式为XXX-XXX-XXX（三段大写英文字母和数字）。注意：O应视为0，I应视为1，Z应视为2。只回复关卡ID，不要回复其他内容。如果图片中没有这样的ID，回复“未找到”。",
                image_urls=[data_url],
                system_prompt="你是一个游戏图片识别助手，只提取关卡ID。",
                request_max_retries=1,
            )
            text = resp.completion_text or ""
            pure_id = parse_id(text) or ""
            logger.info(f"[SMM2] OCR结果: {text.strip()[:100]}, 提取ID: {pure_id}")
            return pure_id
        except Exception as e:
            logger.error(f"[SMM2] OCR调用失败: {e}")
            return ""

    # ==================== /smm2 命令 ====================

    @filter.command("smm2", priority=1)
    async def cmd_smm2(self, event: AstrMessageEvent, id: str = ""):
        """查询马造2关卡或玩家"""
        text = event.get_message_str() or ""
        pure_id = parse_id(text)
        if not pure_id:
            await _send_text(event, "用法：/smm2 <关卡或玩家ID>（9位字符或 XXX-XXX-XXX）")
            return
        pure_id = pure_id.upper()

        quality = "low"
        if self.config:
            quality = (self.config.get("smm2_quality") or "low").strip().lower()

        async with aiohttp.ClientSession() as s:
            level = await fetch_level(s, pure_id)
            if level:
                body = self._fmt_level(pure_id, level)
                if quality == "high":
                    if await _ensure_toost():
                        images = await self._do_render(event, pure_id)
                        if images:
                            await event.send(MessageChain([Plain(body)]))
                            for img_path in images:
                                try:
                                    await event.send(MessageChain([Image(file=img_path)]))
                                except Exception as e:
                                    logger.error(f"[SMM2] 图片发送失败: {e}")
                            for fp in images:
                                try: os.unlink(fp)
                                except: pass
                            return
                    # fallback to low
                    img_url = f"{THUMB_URL}/{pure_id}"
                    await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
                    return
                else:
                    body += f"\n\n查询高清图片：/render {pure_id[0:3]}-{pure_id[3:6]}-{pure_id[6:9]}"
                    img_url = f"{THUMB_URL}/{pure_id}"
                    try:
                        await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
                    except Exception as e:
                        await _send_text(event, body)
                    return

            user_data, lists = await fetch_player(s, pure_id)
            if user_data and isinstance(user_data, dict) and "_status" not in user_data:
                body = self._fmt_player(pure_id, user_data, lists)
                await _send_text(event, body)
                return

        await _send_text(event, f"ID {pure_id} 不存在（关卡/玩家均未找到）")

    def _fmt_level(self, pure_id, d):
        lines = [
            "📊 关卡信息",
            f"关卡ID：{pure_id}",
            f"关卡名称：{d.get('name') or '无名称'}",
            f"玩家ID：{d.get('maker_id') or '无数据'}",
            f"总游玩次数：{d.get('courses_played') or '暂无数据'}",
            f"点赞 / 踩：{d.get('likes') or 0} / {d.get('dislikes') or 0}",
        ]
        cr = d.get("clear_rate")
        if cr is not None:
            try:
                lines.append(f"通关率：{float(cr):.2f}%")
            except (ValueError, TypeError):
                lines.append(f"通关率：{cr}%")
        else:
            lines.append("通关率：暂无数据")
        lines.append(f"对战数据：总场次 {d.get('battle_total') or '无'}，胜利 {d.get('battle_win') or '无'}")
        return "\n".join(lines)

    def _fmt_player(self, mid, u, lists):
        lines = ["👤 玩家信息", f"玩家ID：{mid}", f"玩家名称：{u.get('name') or '无名称'}"]
        mii = u.get("mii_image") or u.get("mii_img_url") or u.get("mii_avatar") or ""
        if mii:
            lines.append(f"Mii头像：{mii}")
        lines.append(f"发布关卡总数：{u.get('uploaded_levels') or '暂无数据'}")
        lines.append(f"创作者点数：{u.get('maker_points') or 0}")
        lines.append(f"总点赞：{u.get('likes') or '暂无数据'}")
        lines.append(f"总踩：{u.get('boos') or '暂无数据'}")
        lines.append(f"总游玩次数：{u.get('courses_played') or '暂无数据'}")
        lines.append(f"总通关人数：{u.get('courses_cleared') or '暂无数据'}")
        lines.append(f"总死亡次数：{u.get('courses_deaths') or '暂无数据'}")
        lines.append(f"对战段位：{u.get('versus_rank_name') or '无段位'}")
        tb = u.get("versus_plays") or 0
        bw = u.get("versus_won") or 0
        bl = u.get("versus_lost") or 0
        wr_pct = "0.00%" if tb == 0 else f"{(bw / tb * 100):.2f}%"
        lines.append(f"总对战场次：{tb}")
        lines.append(f"对战胜利场次：{bw}")
        lines.append(f"对战失败场次：{bl}")
        lines.append(f"对战胜率：{wr_pct}")
        acr = u.get("total_clear_rate")
        lines.append(f"全局平均通关率：{acr}%" if acr else "全局平均通关率：无统计")
        lines.append(f"拥有世界纪录：{u.get('world_records', 0)}个")
        lines.append(f"首通记录总数：{u.get('first_clears', 0)}个")
        lines.append("")
        lines.append("📤 发布的关卡")
        lines.append(fmt_courses(lists["posted"]))
        lines.append("")
        lines.append("❤️ 点赞过的关卡")
        lines.append(fmt_courses(lists["liked"]))
        lines.append("")
        lines.append("🎮 游玩过的关卡")
        lines.append(fmt_courses(lists["played"]))
        lines.append("")
        lines.append("🏆 首通关卡")
        lines.append(fmt_courses(lists["first_clear"]))
        lines.append("")
        lines.append("🌟 持有世界纪录关卡")
        lines.append(fmt_courses(lists["wr"]))
        return "\n".join(lines)

    # ==================== /rest 命令 ====================

    @filter.command("rest", priority=1)
    async def cmd_rest(self, event: AstrMessageEvent, mode: str = ""):
        """随机抽取关卡 bcd"""
        text = event.get_message_str() or ""
        m = re.search(r"\d", text)
        if not m and not mode:
            await _send_text(event, "用法：/rest <0-4>\n0=完全随机 1=简单 2=普通 3=困难 4=极难")
            return
        try:
            mode_int = int(mode or m.group(0))
        except (ValueError, AttributeError):
            await _send_text(event, "难度参数范围：0-4")
            return
        if mode_int not in (0, 1, 2, 3, 4):
            await _send_text(event, "难度参数范围：0-4")
            return

        diff_map = {0: "完全随机", 1: "简单", 2: "普通", 3: "困难", 4: "极难"}
        api_diff = {0: "", 1: "e", 2: "n", 3: "ex", 4: "sex"}
        await _send_text(event, f"⏳ 正在抽取{diff_map[mode_int]}关卡...")

        async with aiohttp.ClientSession() as s:
            qs = f"?difficulty={api_diff[mode_int]}" if api_diff[mode_int] else ""
            data = await api_get(s, f"{SEARCH_ENDLESS}{qs}")
            if not data or (isinstance(data, dict) and "_status" in data):
                await _send_text(event, "抽取失败，请稍后再试")
                return

            pure_id, cname = self._extract_first_id(data)
            if not pure_id:
                await _send_text(event, "未获取到关卡列表")
                return
            pure_id = pure_id.upper().replace("-", "")

            await _send_text(event, f"抽取到关卡 {pure_id}，正在下载 bcd 文件...")
            fpath = await download_bcd(s, pure_id, self._tmp_dir)
            if not fpath:
                await _send_text(event, f"关卡 {pure_id} 的 bcd 文件下载失败")
                return

            await self._send_bcd_file(event, pure_id, cname, fpath)

    @staticmethod
    def _extract_first_id(data):
        if isinstance(data, list) and data:
            first = data[0]
            return (first.get("course_id") or first.get("id"), first.get("name")) if isinstance(first, dict) else (first, "")
        if isinstance(data, dict):
            for key in ("courses", "data", "results", "levels"):
                if isinstance(data.get(key), list) and data[key]:
                    first = data[key][0]
                    return (first.get("course_id") or first.get("id"), first.get("name")) if isinstance(first, dict) else (first, "")
            cid = data.get("course_id") or data.get("id")
            return (cid, data.get("name"))
        return ("", "")

    # ==================== /bcd 命令 ====================

    @filter.command("bcd", priority=1)
    async def cmd_bcd(self, event: AstrMessageEvent, id: str = ""):
        """下载指定关卡 bcd"""
        text = event.get_message_str() or ""
        pure_id = parse_id(text)
        if not pure_id:
            await _send_text(event, "用法：/bcd <关卡ID>")
            return
        pure_id = pure_id.upper()

        await _send_text(event, f"⏳ 正在下载关卡 {pure_id} 的 bcd 文件...")

        async with aiohttp.ClientSession() as s:
            level = await fetch_level(s, pure_id)
            cname = level.get("name") if level else ""
            fpath = await download_bcd(s, pure_id, self._tmp_dir)
            if not fpath:
                await _send_text(event, f"关卡 {pure_id} 的 bcd 文件下载失败")
                return
            await self._send_bcd_file(event, pure_id, cname, fpath)

    # ==================== /render 命令（仅发图） ====================

    @filter.command("render", priority=1)
    async def cmd_render(self, event: AstrMessageEvent, id: str = ""):
        """渲染关卡高清图片（地表+里世界）"""
        text = event.get_message_str() or ""
        pure_id = parse_id(text)
        if not pure_id:
            await _send_text(event, "用法：/render <关卡ID>\n渲染地表和里世界高清图片")
            return
        pure_id = pure_id.upper()

        if not await _ensure_toost():
            await _send_text(event, "渲染器未找到，自动下载也失败，请将 toost 目录放到插件目录下")
            return

        await _send_text(event, f"⏳ 正在渲染关卡 {pure_id} 的图片...")

        images = await self._do_render(event, pure_id)
        if not images:
            return

        # 发图片
        for img_path in images:
            try:
                await event.send(MessageChain([Image(file=img_path)]))
            except Exception as e:
                logger.error(f"[SMM2] 图片发送失败: {e}")

        # 清理
        for fp in images:
            try:
                os.unlink(fp)
            except:
                pass

    async def _do_render(self, event, pure_id):
        """执行渲染，返回图片路径列表"""
        async with aiohttp.ClientSession() as s:
            raw = await api_get(s, f"{LEVEL_DATA}/{pure_id}", as_json=False)
            if not raw or isinstance(raw, dict):
                await _send_text(event, f"关卡 {pure_id} 的 bcd 下载失败")
                return None

            if raw[:2] == b"\x1f\x8b":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass

            bcd_path = os.path.join(TOOST_RENDER, f"{pure_id}.bcd")
            with open(bcd_path, "wb") as f:
                f.write(raw)

            ow_path = os.path.join(TOOST_RENDER, f"{pure_id}_ow.png")
            sw_path = os.path.join(TOOST_RENDER, f"{pure_id}_sw.png")

            cmd = [TOOST_EXE, "-p", bcd_path, "-a", "2", "-r", "-o", ow_path, "-s", sw_path]
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=60, cwd=TOOST_WORK,
                                        encoding="utf-8", errors="replace")
                if result.returncode != 0:
                    await _send_text(event, f"渲染失败：{result.stderr or result.stdout}")
                    try: os.unlink(bcd_path)
                    except: pass
                    return None
            except subprocess.TimeoutExpired:
                await _send_text(event, "渲染超时")
                try: os.unlink(bcd_path)
                except: pass
                return None

            # 清理 bcd
            try: os.unlink(bcd_path)
            except: pass

            images = []
            if os.path.exists(ow_path) and os.path.getsize(ow_path) > 100:
                images.append(ow_path)
            if os.path.exists(sw_path) and os.path.getsize(sw_path) > 100:
                images.append(sw_path)

            if not images:
                await _send_text(event, f"渲染完成但未生成图片")
                return None

            return images

    # ==================== 文件发送 ====================

    async def _send_bcd_file(self, event, pure_id, cname, fpath):
        label = cname or pure_id
        try:
            await event.send(
                MessageChain([
                    Plain(f"关卡 {pure_id}（{label}）bcd 文件"),
                    File(name=f"{pure_id}.bcd", file=fpath),
                ])
            )
        except Exception as e:
            logger.error(f"[SMM2] 发送 bcd 文件失败: {e}")
            await _send_text(event, f"bcd 文件发送失败：{e}")
        finally:
            try:
                os.remove(fpath)
            except OSError as e:
                logger.error(f"[SMM2] 删除临时文件失败: {e}")

        # ==================== LLM Tools ====================

    @llm_tool(name="smm2_image_lookup")
    async def smm2_image_lookup(self, event: AstrMessageEvent, course_id: str):
        """当用户发送图片（如超级马力欧制造2游戏截图、Switch屏幕照片等）时，必须使用此工具。此工具会识别图片中的关卡ID并返回关卡图片和基本信息。注意：用户发送图片时必须使用此工具，不要使用其他smm2查询工具。

        重要规则：
        1. SMM2的ID由9位字符组成（格式为XXX-XXX-XXX），只使用以下字符：0-9、A-Y，不包含字母I、O、Z。
        2. 如果从图片识别到的ID包含O、I、Z，需要纠正：O→0，I→1，Z→2。
        3. 例如图片上看到"MJ-D65-O2G"，O应改为0，变成"MJ-D65-02G"。
        4. 如果图片中有多个ID，优先选择关卡ID（通常在关卡详情页面中显示）。
        5. 如果图片中没有XXX-XXX-XXX格式的ID，不要调用此工具。

        用户提示：{llm_hint}

        Args:
            course_id(string): 从图片中识别到的关卡ID，格式为XXX-XXX-XXX或9位字符，需进行I/O/Z纠错
        """
        if not (self.config and self.config.get("enable_llm_tools", False)):
            yield event.plain_result("LLM工具已关闭")
            return
        raw_id = parse_id(course_id) or ""
        if not raw_id:
            yield event.plain_result("无法从图片中识别到有效的关卡ID")
            return

        async with aiohttp.ClientSession() as s:
            level, used_id = await try_query_level_with_corrections(s, raw_id)
            if not level:
                yield event.plain_result(f"关卡 {raw_id} 不存在")
                return

        pure_id = used_id
        quality = "high"
        if self.config:
            quality = (self.config.get("image_quality") or "high").strip().lower()

        body = self._fmt_level(pure_id, level)

        if quality == "high":
            if await _ensure_toost():
                images = await self._do_render(event, pure_id)
                if images:
                    await event.send(MessageChain([Plain(body)]))
                    for img_path in images:
                        try:
                            await event.send(MessageChain([Image(file=img_path)]))
                        except Exception as e:
                            logger.error(f"[SMM2] 图片发送失败: {e}")
                    for fp in images:
                        try: os.unlink(fp)
                        except: pass
                    yield event.plain_result("已发送关卡信息和图片")
                    return
            # fallback to low
            img_url = f"{THUMB_URL}/{pure_id}"
            await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
            yield event.plain_result("已发送关卡信息和图片")
            return
        else:
            img_url = f"{THUMB_URL}/{pure_id}"
            await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
            yield event.plain_result("已发送关卡信息和图片")
            return
    @llm_tool(name="smm2_query_auto")
    async def smm2_query_auto(self, event: AstrMessageEvent, course_id: str):
        """查询超级马力欧制造2关卡或玩家信息。当用户通过文字消息说"查一下xxx-xxx-xxx"但没明确说是关卡还是玩家时使用此工具。注意：此工具仅用于纯文字消息，如果用户发送了图片，请使用smm2_image_lookup工具。

        重要：SMM2的ID由9位字符组成（格式为XXX-XXX-XXX），只使用以下字符：0-9、A-Y，不包含字母I、O、Z。
        如果识别到的ID包含O、I、Z，需要纠正：O→0，I→1，Z→2。

        用户提示：{llm_hint}

        Args:
            course_id(string): 关卡或玩家ID，格式为XXX-XXX-XXX或9位字符
        """
        if not (self.config and self.config.get("enable_llm_tools", False)):
            yield event.plain_result("LLM工具已关闭")
            return

        raw_id = parse_id(course_id) or ""
        if not raw_id:
            yield event.plain_result("无法识别ID格式，请提供XXX-XXX-XXX格式的ID")
            return

        async with aiohttp.ClientSession() as s:
            level, used_id = await try_query_level_with_corrections(s, raw_id)
            if level:
                pure_id = used_id
                body = self._fmt_level(pure_id, level)
                quality = "low"
                if self.config:
                    quality = (self.config.get("smm2_quality") or "low").strip().lower()
                if quality == "high":
                    if await _ensure_toost():
                        images = await self._do_render(event, pure_id)
                        if images:
                            await event.send(MessageChain([Plain(self._fmt_level(pure_id, level))]))
                            for img_path in images:
                                try:
                                    await event.send(MessageChain([Image(file=img_path)]))
                                except Exception as e:
                                    logger.error(f"[SMM2] 图片发送失败: {e}")
                            for fp in images:
                                try: os.unlink(fp)
                                except: pass
                            yield event.plain_result("已发送关卡信息和图片")
                            return
                    # fallback to low
                    body = self._fmt_level(pure_id, level)
                    body += f"\n\n查询高清图片：/render {pure_id[0:3]}-{pure_id[3:6]}-{pure_id[6:9]}"
                    img_url = f"{THUMB_URL}/{pure_id}"
                    await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
                    yield event.plain_result("已发送关卡信息和图片")
                    return
                else:
                    body = self._fmt_level(pure_id, level)
                    body += f"\n\n查询高清图片：/render {pure_id[0:3]}-{pure_id[3:6]}-{pure_id[6:9]}"
                    img_url = f"{THUMB_URL}/{pure_id}"
                    await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
                    yield event.plain_result("已发送关卡信息和图片")
                    return

            pure_id = fix_smm2_id(raw_id)
            user_data, lists = await fetch_player(s, pure_id)
            if user_data and isinstance(user_data, dict) and "_status" not in user_data:
                body = self._fmt_player(pure_id, user_data, lists)
                yield event.chain_result([Plain(body)])
                return

        yield event.plain_result(f"ID {raw_id} 不存在（关卡/玩家均未找到）")

    @llm_tool(name="smm2_query_course")
    async def smm2_query_course(self, event: AstrMessageEvent, course_id: str):
        """查询超级马力欧制造2关卡信息。当用户通过文字明确说"查一下xxx这个关卡"或"这个关卡"时使用此工具，只查关卡不查玩家。如果关卡不存在则返回错误。注意：此工具仅用于纯文字消息，如果用户发送了图片，请使用smm2_image_lookup工具。

        重要：SMM2的ID由9位字符组成（格式为XXX-XXX-XXX），只使用以下字符：0-9、A-Y，不包含字母I、O、Z。
        如果识别到的ID包含O、I、Z，需要纠正：O→0，I→1，Z→2。

        用户提示：{llm_hint}

        Args:
            course_id(string): 关卡ID，格式为XXX-XXX-XXX或9位字符
        """
        if not (self.config and self.config.get("enable_llm_tools", False)):
            yield event.plain_result("LLM工具已关闭")
            return

        raw_id = parse_id(course_id) or ""
        if not raw_id:
            yield event.plain_result("无法识别ID格式，请提供XXX-XXX-XXX格式的ID")
            return

        async with aiohttp.ClientSession() as s:
            level, used_id = await try_query_level_with_corrections(s, raw_id)
            if level:
                pure_id = used_id
                body = self._fmt_level(pure_id, level)
                quality = "low"
                if self.config:
                    quality = (self.config.get("smm2_quality") or "low").strip().lower()
                if quality == "high":
                    if await _ensure_toost():
                        images = await self._do_render(event, pure_id)
                        if images:
                            await event.send(MessageChain([Plain(self._fmt_level(pure_id, level))]))
                            for img_path in images:
                                try:
                                    await event.send(MessageChain([Image(file=img_path)]))
                                except Exception as e:
                                    logger.error(f"[SMM2] 图片发送失败: {e}")
                            for fp in images:
                                try: os.unlink(fp)
                                except: pass
                            yield event.plain_result("已发送关卡信息和图片")
                            return
                    # fallback to low
                    body = self._fmt_level(pure_id, level)
                    body += f"\n\n查询高清图片：/render {pure_id[0:3]}-{pure_id[3:6]}-{pure_id[6:9]}"
                    img_url = f"{THUMB_URL}/{pure_id}"
                    await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
                    yield event.plain_result("已发送关卡信息和图片")
                    return
                else:
                    body = self._fmt_level(pure_id, level)
                    body += f"\n\n查询高清图片：/render {pure_id[0:3]}-{pure_id[3:6]}-{pure_id[6:9]}"
                    img_url = f"{THUMB_URL}/{pure_id}"
                    await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
                    yield event.plain_result("已发送关卡信息和图片")
                    return

        yield event.plain_result(f"关卡 {raw_id} 不存在")

    @llm_tool(name="smm2_query_player")
    async def smm2_query_player(self, event: AstrMessageEvent, course_id: str):
        """查询超级马力欧制造2玩家信息。当用户通过文字明确说"查一下xxx这个人/玩家/用户"时使用此工具，只查玩家不查关卡。如果玩家不存在则返回错误。注意：此工具仅用于纯文字消息，如果用户发送了图片，请使用smm2_image_lookup工具。

        重要：SMM2的ID由9位字符组成（格式为XXX-XXX-XXX），只使用以下字符：0-9、A-Y，不包含字母I、O、Z。
        如果识别到的ID包含O、I、Z，需要纠正：O→0，I→1，Z→2。

        用户提示：{llm_hint}

        Args:
            course_id(string): 玩家ID，格式为XXX-XXX-XXX或9位字符
        """
        if not (self.config and self.config.get("enable_llm_tools", False)):
            yield event.plain_result("LLM工具已关闭")
            return

        raw_id = parse_id(course_id) or ""
        if not raw_id:
            yield event.plain_result("无法识别ID格式，请提供XXX-XXX-XXX格式的ID")
            return

        async with aiohttp.ClientSession() as s:
            user_data, lists = await fetch_player(s, fix_smm2_id(raw_id))
            if user_data and isinstance(user_data, dict) and "_status" not in user_data:
                body = self._fmt_player(fix_smm2_id(raw_id), user_data, lists)
                yield event.chain_result([Plain(body)])
                return

        yield event.plain_result(f"玩家 {raw_id} 不存在")

    @llm_tool(name="smm2_random_course")
    async def smm2_random_course(self, event: AstrMessageEvent, difficulty: str = ""):
        """随机抽取一个超级马力欧制造2关卡并发送关卡图片和基本信息。当用户说"抽一个关卡"、"随机来一个"、"来个难的"等时使用此工具。

        用户提示：{llm_hint}

        Args:
            difficulty(string): 难度参数，可选值：0=完全随机 1=简单 2=普通 3=困难 4=极难。不填默认为0。
        """
        if not (self.config and self.config.get("enable_llm_tools", False)):
            yield event.plain_result("LLM工具已关闭")
            return
        if not (self.config and self.config.get("enable_llm_random", True)):
            yield event.plain_result("随机抽关卡工具已关闭")
            return

        try:
            mode_int = int(difficulty) if difficulty else 0
        except ValueError:
            mode_int = 0
        if mode_int not in (0, 1, 2, 3, 4):
            mode_int = 0

        diff_map = {0: "完全随机", 1: "简单", 2: "普通", 3: "困难", 4: "极难"}
        api_diff = {0: "", 1: "e", 2: "n", 3: "ex", 4: "sex"}

        async with aiohttp.ClientSession() as s:
            qs = f"?difficulty={api_diff[mode_int]}" if api_diff[mode_int] else ""
            data = await api_get(s, f"{SEARCH_ENDLESS}{qs}")
            if not data or (isinstance(data, dict) and "_status" in data):
                yield event.plain_result("抽取失败，请稍后再试")
                return

            pure_id, cname = self._extract_first_id(data)
            if not pure_id:
                yield event.plain_result("未获取到关卡列表")
                return
            pure_id = pure_id.upper().replace("-", "")

            level, used_id = await try_query_level_with_corrections(s, pure_id)
            if level:
                pure_id = used_id
                body = f"抽取到{diff_map[mode_int]}关卡\n" + self._fmt_level(pure_id, level)

                quality = "high"
                if self.config:
                    quality = (self.config.get("image_quality") or "high").strip().lower()

                if quality == "high" and await _ensure_toost():
                    images = await self._do_render(event, pure_id)
                    if images:
                        await event.send(MessageChain([Plain(body)]))
                        for img_path in images:
                            try:
                                await event.send(MessageChain([Image(file=img_path)]))
                            except Exception as e:
                                logger.error(f"[SMM2] 图片发送失败: {e}")
                        for fp in images:
                            try: os.unlink(fp)
                            except: pass
                        event.stop_event()
                        return

                img_url = f"{THUMB_URL}/{pure_id}"
                await event.send(MessageChain([Plain(body), Image(file=img_url, url=img_url)]))
                event.stop_event()
                return
            else:
                img_url = f"{THUMB_URL}/{pure_id}"
                yield event.chain_result([Plain(f"抽取到{diff_map[mode_int]}关卡 {pure_id}"), Image(file=img_url, url=img_url)])

