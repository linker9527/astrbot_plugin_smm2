# astrbot_plugin_smm2

超级马力欧制造2（Super Mario Maker 2）AstrBot 插件。

支持关卡/玩家查询、随机抽图、bcd 文件下载、关卡高清渲染。

## 功能

### 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/smm2 <ID>` | 查询关卡或玩家信息 | `/smm2 0c7-1bx-j2g` |
| `/rest <0-4>` | 随机抽关卡并下载 bcd | `/rest 2` |
| `/bcd <ID>` | 下载指定关卡 bcd 文件 | `/bcd 3FG-2K1-7HG` |
| `/render <ID>` | 渲染关卡高清图片（地表+里世界） | `/render WYQ-CPL-90H` |

ID 格式：9位字符或 `XXX-XXX-XXX`，不区分大小写。先查关卡，查不到再查玩家。

### /rest 难度参数

| 参数 | 难度 |
|------|------|
| 0 | 完全随机 |
| 1 | 简单 |
| 2 | 普通 |
| 3 | 困难 |
| 4 | 极难 |

### /render 说明

使用 [toost](https://github.com/TheGreatRambler/toost) v2.0.2 渲染关卡地表和里世界高清全图（2倍缩放，去网格），两张 PNG 图片直接发送到聊天中，并附带 bcd 文件。

**首次使用自动安装：** 第一次执行 `/render` 时，插件会自动从 GitHub 下载 toost 渲染器并解压到插件目录，无需手动操作。下载进度会在 AstrBot 日志中显示进度条。

**手动安装（备用）：** 如果自动下载失败，可前往 [toost Releases](https://github.com/TheGreatRambler/toost/releases) 下载 `toost_windows.zip`，解压后将 `toost` 文件夹放到插件目录下（与 `main.py` 同级）。

**注意：** toost 不支持中文路径，请确保 AstrBot 安装路径不含中文。

## 数据来源

- 关卡和玩家数据：[tgrcode.com](https://tgrcode.com/) 公开 API
- 关卡渲染：[toost](https://github.com/TheGreatRambler/toost) v2.0.2

## 联系方式

有问题或建议欢迎反馈：

- QQ: 584017206
- 邮箱: qfqfg_w@qq.com

## 更新日志

### v1.1.1

- `/render` 首次使用时自动下载 toost 渲染器，附带下载进度条
- 不再随插件分发 toost 文件，插件体积大幅减小

### v1.1.0

- 新增 `/render <ID>` 命令：渲染关卡高清图片（地表+里世界）+ 发送 bcd 文件
- `/smm2` 查询结果末尾增加 `/render` 命令提示
- 代码优化和路径便携化

### v1.0.1

- 初始版本：`/smm2` `/rest` `/bcd` 三个命令