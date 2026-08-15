# -*- coding: utf-8 -*-
"""
AstrBot Plugin: SMM2 (Super Mario Maker 2)
关卡查询 / 玩家查询 / 随机抽图 / bcd 下载 / 关卡渲染
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
from astrbot.api.message_components import Plain, Image, File
from astrbot.api.all import MessageChain
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

TOOST_DOWNLOAD_URL = (
    "https://github.com/TheGreatRambler/toost/"
    "releases/latest/download/toost_windows.zip"
)
TOOST_ZIP_PATH = os.path.join(_PLUGIN_DIR, "_tmp", "toost_windows.zip")
TOOST_DOWNLOADED = False


async def _ensure_toost():
    """如果 toost 不存在，自动从 GitHub 下载并解压"""
    global TOOST_DOWNLOADED
    if TOOST_DOWNLOADED:
        return True
    if os.path.exists(TOOST_EXE):
        TOOST_DOWNLOADED = True
        return True

    logger.info("[SMM2] 检测到 toost 不存在，正在自动下载...")
    try:
        os.makedirs(os.path.join(_PLUGIN_DIR, "_tmp"), exist_ok=True)
        async with aiohttp.ClientSession() as s:
            async with s.get(TOOST_DOWNLOAD_URL, timeout=120) as r:
                if r.status != 200:
                    logger.error(f"[SMM2] 下载 toost 失败 HTTP {r.status}")
                    return False
                total = int(r.headers.get("Content-Length", "0"))
                downloaded = 0
                bar_len = 30
                async for chunk in r.content.iter_chunked(8192):
                    downloaded += len(chunk)
                    with open(TOOST_ZIP_PATH, "ab") as f:
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
                data = None
        with zipfile.ZipFile(TOOST_ZIP_PATH, "r") as z:
            z.extractall(os.path.join(_PLUGIN_DIR, "toost"))
        try:
            os.unlink(TOOST_ZIP_PATH)
        except Exception:
            pass
        if os.path.exists(TOOST_EXE):
            TOOST_DOWNLOADED = True
            logger.info("[SMM2] toost 自动下载完成")
            return True
        logger.error("[SMM2] toost 解压后 exe 未找到")
        return False
    except Exception as e:
        logger.error(f"[SMM2] 下载 toost 失败: {e}")
        return False


# ============ 工具函数 ============

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
    "超级马力欧制造2关卡/玩家查询、随机抽图、bcd下载、关卡渲染",
    "1.1.1",
    "https://github.com/linker9527/astrbot_plugin_smm2",
)
class Smm2Plugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._tmp_dir = os.path.join(_PLUGIN_DIR, "_tmp")
        os.makedirs(self._tmp_dir, exist_ok=True)
        os.makedirs(TOOST_RENDER, exist_ok=True)

    # ---------- /smm2 ----------

    @filter.command("smm2", priority=1)
    async def cmd_smm2(self, event: AstrMessageEvent, id: str = ""):
        """查询马造2关卡或玩家"""
        text = event.get_message_str() or ""
        pure_id = parse_id(text)
        if not pure_id:
            await _send_text(event, "用法：/smm2 <关卡或玩家ID>（9位字符或 XXX-XXX-XXX）")
            return
        pure_id = pure_id.upper()

        async with aiohttp.ClientSession() as s:
            level = await fetch_level(s, pure_id)
            if level:
                body = self._fmt_level(pure_id, level)
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

    # ---------- /rest ----------

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

    # ---------- /bcd ----------

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

    # ---------- /render ----------

    @filter.command("render", priority=1)
    async def cmd_render(self, event: AstrMessageEvent, id: str = ""):
        """渲染关卡高清图片（地表+里世界）"""
        text = event.get_message_str() or ""
        pure_id = parse_id(text)
        if not pure_id:
            await _send_text(event, "用法：/render <关卡ID>\n渲染地表和里世界高清图片")
            return
        pure_id = pure_id.upper()

        if not os.path.exists(TOOST_EXE):
            downloaded = await _ensure_toost()
            if not downloaded:
                await _send_text(event, "渲染器 toost 自动下载失败，请稍后重试")
                return

        await _send_text(event, f"⏳ 正在渲染关卡 {pure_id} 的图片...")

        async with aiohttp.ClientSession() as s:
            raw = await api_get(s, f"{LEVEL_DATA}/{pure_id}", as_json=False)
            if not raw or isinstance(raw, dict):
                await _send_text(event, f"关卡 {pure_id} 的 bcd 下载失败")
                return

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
                    return
            except subprocess.TimeoutExpired:
                await _send_text(event, "渲染超时")
                try: os.unlink(bcd_path)
                except: pass
                return

            level = await fetch_level(s, pure_id)
            label = level.get("name") if level else ""

            # 发图片
            chains = []
            if os.path.exists(ow_path) and os.path.getsize(ow_path) > 100:
                chains.append(MessageChain([Image(file=ow_path)]))
            if os.path.exists(sw_path) and os.path.getsize(sw_path) > 100:
                chains.append(MessageChain([Image(file=sw_path)]))

            sent_ow = False
            sent_sw = False
            for i, chain in enumerate(chains):
                try:
                    await event.send(chain)
                    if i == 0:
                        sent_ow = True
                    else:
                        sent_sw = True
                except Exception as e:
                    logger.error(f"[SMM2] 图片发送失败: {e}")

            msg = f"{pure_id}（{label}）" if label else f"{pure_id}"
            msg += " ✅ 地表" if sent_ow else " ❌ 地表"
            msg += " + 里世界 完成" if sent_sw else " + 里世界"
            await _send_text(event, msg)

            # 发 bcd
            sent_bcd = False
            try:
                await event.send(MessageChain([
                    Plain(f"关卡 {pure_id} bcd 文件"),
                    File(name=f"{pure_id}.bcd", file=bcd_path),
                ]))
                sent_bcd = True
            except Exception as e:
                logger.error(f"[SMM2] bcd 发送失败: {e}")

            # 全部清理
            for fp in [bcd_path, ow_path, sw_path]:
                try: os.unlink(fp)
                except: pass

    # ---------- 文件发送 ----------

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
