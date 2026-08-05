# A股热度

一个 Windows 10/11 x64 原生桌面工具。用户手动刷新后，软件独立统计所选时间范围内的同花顺新闻提及热度，并低频读取东方财富官方公开的 A 股人气榜与飙升榜。

> 热度统计仅用于信息整理，不构成任何投资建议。

## 下载与安装

Windows x64 安装包可从 [v0.1.0 Release](https://github.com/3154638220/ashare-hotpot/releases/tag/v0.1.0) 下载：
[AshareHotPot-Setup-0.1.0-x64.exe](https://github.com/3154638220/ashare-hotpot/releases/download/v0.1.0/AshareHotPot-Setup-0.1.0-x64.exe)。

## 功能

- PySide6 原生桌面界面，不启动浏览器或本地 Web 服务。
- 同花顺端接入公司资讯、个股聚焦、公司研究、行业研究和证券市场新闻。
- 东方财富端低频读取官方公开的 A 股人气榜 Top 100 与飙升榜 Top 100；官方只公布排名与较昨日变动，权重未公开，软件不重算热度值。
- 顶部来源卡片可切换同花顺新闻榜和东方财富综合人气榜；人气榜/飙升榜可在卡片内切换，两个来源完全分开统计、筛选和查看明细。
- 过滤融资余额、ETF 资金、固定格式资金流、股东户数和批量机构调研等模板稿。
- 对跨栏目重复 URL 和高相似事件去重，一件事对同一股票最多计一次。
- 排名可搜索、排序；点击股票名称可查看同花顺的支撑稿件，或打开东方财富官方个股人气页。
- SQLite 缓存与快照存放在 `%LOCALAPPDATA%\AshareHotPot`，升级安装不会覆盖。
- 观察窗口仅影响同花顺新闻榜（1–168 小时，默认 24 小时）；官方人气榜显示截至读取时间，不暗示对应某小时窗口。
- 官方榜单距上次成功读取不足 10 分钟时复用缓存；身份核实、空数据或结构变化视为整榜失败，不展示部分榜，并保留上次成功榜单、醒目标注过期与失败原因。

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

默认测试完全离线。若要小规模验证当前同花顺和东方财富页面结构，可显式运行：

```powershell
$env:ASHARE_HOTPOT_LIVE_TEST = "1"
python -m pytest -m live
```

## 数据口径与限制

- 统计窗口由界面的“观察窗口”选择器决定，仅作用于同花顺新闻榜，范围为 1–168 小时，默认 24 小时。
- 同花顺榜按有效去重新闻事件数降序；东方财富官方榜按官方公布排名展示（人气榜为综合关注度名次，飙升榜为较昨日排名提升最多的股票），并列时按官方返回顺序。
- 按东方财富公开口径，综合人气由访问、关注和社区互动共同构成（[榜单规则](https://gbcdn.dfcfw.com/rank/)）；软件不展示或推导任何未公开的热度数值。
- 纳入沪深北交易所 A 股及 ST 股票，排除 B 股、基金、债券、指数、概念和港美股。
- 同花顺列表公开分页有限。软件会结合本地缓存，但手动刷新间隔过长时仍可能存在遗漏。
- 东方财富官方综合人气仅作为散户整体关注度的代理，不能解释为情绪、资金流向或交易建议；其热度权重未公开，软件不进行可复算的推测。
- 抓取器使用有限并发、请求间隔和失败重试；请勿改造成高频或商业化采集器，正式分发前应自行核对来源站点的使用条款。

## 项目结构

- `src/ashare_hotpot/`：采集、解析、过滤、去重、SQLite、Qt 界面和后台线程。
- `tests/`：离线解析、排名、存储、刷新流程和 Qt 工作线程测试。
- `ashare_hotpot.spec`：PyInstaller `onedir` 配置。
- `installer/AshareHotPot.iss`：Inno Setup 当前用户安装包配置。
# Codex CLI + DeepSeek

在项目根目录运行（推荐；不受 PowerShell 执行策略影响）：

```powershell
.\start_codex_deepseek.cmd
```

也可以双击该 `.cmd` 文件。若本机 PowerShell 已允许执行本地脚本，也可以运行：

```powershell
.\start_codex_deepseek.ps1
```

脚本使用项目内的 Codex CLI、独立的 DeepSeek 配置和 `deepseek-v4-flash`。如果当前终端没有
`DEEPSEEK_API_KEY`，启动时会以隐藏输入提示 API key；密钥只在该次启动期间保留，不会写入仓库或用户配置。

如需在当前 PowerShell 会话中预先设置密钥，可以运行：

```powershell
$env:DEEPSEEK_API_KEY = "sk-你的 DeepSeek API Key"
.\start_codex_deepseek.ps1
```

首次克隆项目但没有本地 Codex CLI 时，执行一次：

```powershell
Push-Location .codex-cli
npm install
Pop-Location
```

本配置只使用 DeepSeek 原生支持 Codex Responses API 的 `deepseek-v4-flash`，不改动用户全局的 Codex 配置。
