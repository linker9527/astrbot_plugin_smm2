# astrbot_plugin_smm2

超级马力欧制造2（Super Mario Maker 2）AstrBot 插件。

支持关卡/玩家查询、随机抽图、bcd 文件下载、关卡高清渲染，以及 LLM 自然语言调用和图片识别。

## 功能

### 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/smm2 <ID>` | 查询关卡或玩家信息 | `/smm2 0c7-1bx-j2g` |
| `/rest <0-4>` | 随机抽关卡并下载 bcd | `/rest 2` |
| `/bcd <ID>` | 下载指定关卡 bcd 文件 | `/bcd 3FG-2K1-7HG` |
| `/render <ID>` | 渲染关卡高清图片（地表+里世界） | `/render WYQ-CPL-90H` |
| `/help` | 显示帮助 | `/help` |

ID 格式：9位字符或 `XXX-XXX-XXX`，不区分大小写。先查关卡，查不到再查玩家。

### LLM 自然语言调用

在插件配置中开启 **LLM工具总开关** 后，用户无需输入命令，直接说话即可触发查询：

| 场景 | 示例 | 行为 |
|------|------|------|
| 图片 OCR 识别 | 发送 Switch 截图 | 自动提取关卡ID，渲染高清图+返回关卡信息 |
| 模糊查询 | "查一下 WYQ-CPL-90H" | 先查关卡，查不到再查玩家 |
| 只查关卡 | "查一下 WYQ-CPL-90H 这个关卡" | 只查关卡 |
| 只查玩家 | "查一下 WYQ-CPL-90H 这个人" | 只查玩家 |
| 随机抽关卡 | "抽个关卡"、"来个难的" | 随机抽取指定难度关卡并返回图片+信息 |

**OCR 纠错：** SMM2 的 ID 不含字母 I、O、Z。如果图片识别到这些字母，会自动纠正（O→0、I→1、I→L、I→7），最多尝试 3 种方案。

### 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| smm2_quality | /smm2 和文字查询的图片质量（低清/高清） | low |
| image_quality | 图片识别返回的图片质量（低清/高清） | low |
| enable_llm_tools | **LLM工具总开关**，关闭后纯命令模式，不消耗 token | false |
| enable_ocr | 图片OCR识别（需总开关开启） | false |
| enable_llm_auto | 模糊查询（需总开关开启） | true |
| enable_llm_course | 只查关卡（需总开关开启） | true |
| enable_llm_player | 只查玩家（需总开关开启） | true |
| enable_llm_random | 随机抽关卡（需总开关开启） | true |
| llm_hint | 给LLM的额外提示（需总开关开启） | - |

总开关关闭后，下面所有子开关自动隐藏。

### /rest 难度参数

| 参数 | 难度 |
|------|------|
| 0 | 完全随机 |
| 1 | 简单 |
| 2 | 普通 |
| 3 | 困难 |
| 4 | 极难 |

### toost 安装

v1.1.1 / v1.2.1（插件市场版）：toost 会在首次使用 `/render` 时自动从网盘/GitHub 下载，无需手动操作。

手动安装（适用于离线环境）：

1. 前往 [toost Releases](https://github.com/TheGreatRambler/toost/releases) 下载 `toost_windows.zip`
2. 解压后将 `toost` 文件夹放到插件目录下（与 `main.py` 同级）
3. 目录结构：
   ```
   astrbot_plugin_smm2/
   ├── main.py
   ├── toost/
   │   ├── bin/toost.exe
   │   ├── fonts/
   │   └── img/
   └── ...
   ```

**注意：** toost 不支持中文路径，请确保 AstrBot 安装路径不含中文。

## 数据来源

- 关卡和玩家数据：[tgrcode.com](https://tgrcode.com/) 公开 API
- 关卡渲染：[toost](https://github.com/TheGreatRambler/toost) v2.0.2

## 联系方式

有问题或建议欢迎反馈：

- QQ: 584017206
- 邮箱: qfqfg_w@qq.com

## 图片识别原理

v1.2.0 支持用户直接发送 Switch 截图识别关卡 ID，整个链路如下：

1. **拦截器触发**：通过 `@filter.regex(r".*", priority=5)` 注册一个全局拦截器，优先级高于 LLM handler。当消息中包含 `Image` 组件时进入图片处理流程。需在配置中开启 `enable_ocr`。

2. **文字优先提取**：先检查消息文字部分是否已包含 `XXX-XXX-XXX` 格式的 ID。如果有，直接拿来用，跳过 OCR。

3. **OCR 识别**：纯图片消息走 OCR 路径：
   - 下载 QQ 图片到本地临时文件
   - 转为 `data:image/jpeg;base64,...` 格式
   - 遍历所有已配置的 Chat Completion Provider，找到支持图片模态（`modalities` 含 `"image"`）的 provider
   - 调用 `text_chat(prompt="提取SMM2关卡ID", image_urls=[base64_url])` 做视觉识别
   - 从返回文本中用正则提取 9 位 ID

4. **OCR 纠错**：SMM2 的 ID 字符集为 `0-9, A-Y`（不含 I、O、Z）。如果 OCR 识别到 I/O/Z，按 `O→0、I→1、I→L、I→7` 依次尝试纠正，最多生成 3 种候选方案逐一查询 API。

5. **查询与渲染**：拿到 ID 后调用 TGRcode API 查询关卡信息，再根据 `image_quality` 配置决定渲染方式：
   - `high`：调用 toost 渲染地表 + 里世界两张高清全图（2倍缩放，去网格）
   - `low`：直接使用 TGRcode API 返回的缩略图

6. **发送与收尾**：渲染完成后通过 `event.send()` 发送图片到聊天，调用 `stop_event()` 阻止后续 LLM 请求，最后清理临时文件。

7. **失败兜底**：如果 OCR 失败、提取不到 ID、或关卡不存在，拦截器放行，消息正常进入 LLM 对话流程，不会影响其他功能。

**为什么不用主 LLM 直接识别？** AstrBot 的 fallback 机制会在主模型不支持图片时切换到备用模型，但备用模型拿不到 LLM 工具（`tools=[]`），无法调用 function calling。因此拦截器在 LLM 之前自行完成 OCR → 查询 → 渲染的完整链路，不依赖 LLM 工具链。

## 更新日志

### v1.2.1

- 新增 toost 自动下载：首次使用 `/render` 时自动从网盘/GitHub 下载并解压，无需手动放置
- 双源下载：网盘直链优先（`now61.cn`），GitHub Releases 兜底
- 下载进度条显示，SSL 证书兼容（国内服务器自动跳过验证）
- LLM 工具中的 toost 检查统一改为 `_ensure_toost()`，确保首次使用自动触发下载
- 单次会话下载标志，避免重复下载

### v1.2.0

- 新增 LLM 工具总开关（`enable_llm_tools`），关闭后纯命令模式，零 token 消耗
- 新增图片 OCR 识别：用户发 Switch 截图，自动提取关卡 ID → 查询 → 渲染高清图
- 新增 LLM 自然语言调用（需开启总开关）：
  - 图片识别（`enable_ocr`）：发送截图自动提取关卡ID
  - 模糊查询（`enable_llm_auto`）：用户说"查一下xxx"，先查关卡再查玩家
  - 只查关卡（`enable_llm_course`）：用户说"查一下xxx这个关卡"，只查关卡
  - 只查玩家（`enable_llm_player`）：用户说"查一下xxx这个人"，只查玩家
  - 随机抽关卡（`enable_llm_random`）：用户说"抽个关卡"、"来个难的"，随机抽取并返回图片+信息
- 所有 LLM 子开关支持条件显示：总开关关闭后子选项自动隐藏
- OCR 纠错：SMM2 ID 不含 I/O/Z，自动纠正（O→0、I→L/1/7），最多尝试 3 种方案
- LLM 工具发图后补充 `yield` 结果，避免 LLM 收到空返回
- `/render` 命令改为仅发送 2 张高清渲染图，不再发送 bcd 文件
- 渲染逻辑抽为 `_do_render()` 方法供命令和 LLM Tool 共用
- 配置项 `smm2_quality` 和 `image_quality` 支持下拉选择

### v1.1.1

- 面向 AstrBot 插件市场发布的精简版（151KB）
- 移除 toost 大文件（exe + 字体 + 素材），改为首次使用 `/render` 时自动下载
- 双下载源：网盘直链优先，GitHub Releases 兜底
- 下载进度条显示
- SSL 证书兼容：国内服务器访问 GitHub 自动跳过证书验证
- 单次会话下载标志，避免重复下载

### v1.1.0

- 新增 `/render <ID>` 命令：渲染关卡高清图片（地表+里世界）+ 发送 bcd 文件
- `/smm2` 查询结果末尾增加 `/render` 命令提示
- 代码优化和路径便携化

### v1.0.1

- 初始版本：`/smm2` `/rest` `/bcd` 三个命令
