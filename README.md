# A股新闻热度

一个 Windows 10/11 x64 原生桌面工具。用户手动刷新后，软件读取同花顺股票频道多个公开新闻栏目，统计所选时间范围内各 A 股被去重新闻事件提及的次数。

> 新闻提及热度仅用于信息整理，不构成任何投资建议。

## 功能

- PySide6 原生桌面界面，不启动浏览器或本地 Web 服务。
- 接入公司资讯、个股聚焦、公司研究、行业研究和证券市场新闻。
- 过滤融资余额、ETF 资金、固定格式资金流、股东户数和批量机构调研等模板稿。
- 对跨栏目重复 URL 和高相似事件去重，一件事对同一股票最多计一次。
- 排名可搜索、排序；双击股票可查看所有支撑稿件并打开原文。
- SQLite 缓存与快照存放在 `%LOCALAPPDATA%\AshareHotPot`，升级安装不会覆盖。
- 可选择向前爬取 1–168 小时，默认 24 小时。
- 清楚展示每个栏目的实际覆盖起点；公开分页不足时不会声称覆盖了完整所选窗口。

## 本地开发

需要 Python 3.11 或更高版本（推荐 3.12）。

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ashare_hotpot
```

也可以运行：

```powershell
.\scripts\run.ps1
```

如需把数据写到项目内的临时目录，可在启动前设置：

```powershell
$env:ASHARE_HOTPOT_DATA_DIR = "$PWD\data"
python -m ashare_hotpot
```

## Windows 构建

生成 PyInstaller `onedir` 目录：

```powershell
.\scripts\build.ps1 -SkipInstaller
```

如果本机 PowerShell 禁止直接运行脚本，可使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1 -SkipInstaller
```

输出位于 `dist\AshareHotPot\AshareHotPot.exe`。生成标准安装包还需要安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)：

```powershell
.\scripts\build.ps1
```

安装包输出到 `dist\installer`。安装按当前用户进行，默认不需要管理员权限。第一版没有代码签名，Windows SmartScreen 可能在首次运行时显示提示。

## 测试

```powershell
python -m pytest
```

默认测试完全离线。若要小规模验证当前同花顺页面结构，可显式运行：

```powershell
$env:ASHARE_HOTPOT_LIVE_TEST = "1"
python -m pytest -m live
```

## 数据口径与限制

- 统计窗口由界面的“向前爬取”选择器决定，范围为 1–168 小时，默认 24 小时。
- 榜单按有效去重事件数降序；并列时按最近提及时间、股票代码排序。
- 纳入沪深北交易所 A 股及 ST 股票，排除 B 股、基金、债券、指数、概念和港美股。
- 同花顺列表公开分页有限。软件会结合本地缓存，但手动刷新间隔过长时仍可能存在遗漏。
- 抓取器使用有限并发、请求间隔和失败重试；请勿改造成高频或商业化采集器，正式分发前应自行核对来源站点的使用条款。

## 项目结构

- `src/ashare_hotpot/`：采集、解析、过滤、去重、SQLite、Qt 界面和后台线程。
- `tests/`：离线解析、排名、存储、刷新流程和 Qt 工作线程测试。
- `ashare_hotpot.spec`：PyInstaller `onedir` 配置。
- `installer/AshareHotPot.iss`：Inno Setup 当前用户安装包配置。
