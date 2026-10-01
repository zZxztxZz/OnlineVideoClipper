# OnlineVideoClipper

面向 **素材快速获取** 的 Windows 视频工作台：粘贴链接、预览、选镜头、微调边界，然后保存需要的素材。

支持 YouTube、B站和抖音的单条视频。解析和下载在本机完成，无需第三方在线解析 API。

当前版本：**0.11.0**。项目仍在持续完善，平台接口和可用格式可能变化。

## 能做什么

- 独立桌面窗口，便携运行；关闭窗口后缩到系统托盘，后台下载继续。
- 统一素材播放器：预览、逐帧和截图在同一画面完成，共用原素材缓存。
- 按需缓冲、跳转优先、播放预取、最近使用回收，时间轴显示已缓冲范围。
- 在线预览，选择完整视频或多个片段，选择实际可用清晰度，自动合并音画。
- **逐帧微调起点/终点**：先大致定位，再看实际画面按帧调整；自动吸附切镜为可选辅助。
- **保存当前帧**：按视频所选清晰度的原尺寸保存 PNG/JPG，可逐帧挑选截图。
- 精确下载镜头，复用原清晰度的分析缓存；预览用的小尺寸代理不参与最终导出。
- 每次下载可选择保存位置和文件名，同名文件自动编号；记住最近保存目录和导出偏好。
- 持久化队列、暂停/继续、取消、失败重试、调整等待顺序和优先下载。
- B站多分P显示标题并点击切换，单P不显示额外控件。
- 在程序自己的平台连接窗口取得 Cookie，无需读取外部浏览器配置。

## Windows 用户如何运行

源码 ZIP 不是可直接运行的便携程序。可从维护者发布的便携包解压运行，或按下文自行构建。

1. 保持整个便携目录完整，双击 `OnlineVideoClipper.exe`。
2. 粘贴视频链接或分享文本，点击“解析视频”。
3. 下载全片：点击“下载完整视频”。下载片段：填写时间或用 I / O 标记，添加片段后点击“下载所选片段”。
4. 需要精确边界：先标记起止时间，点击“查看 / 微调起点/终点”，用左右方向键或按钮调整，直接下载当前选区。要图片时点击预览下方“保存当前帧”。
5. 在保存窗口改名或点击“另存为…”，然后开始下载。

Windows 10 / 11 的 64 位环境为主要目标，需要 Microsoft Edge WebView2 Runtime 和 .NET Framework 4.6.2 或更新版本。便携程序无需安装 Python；若缺少 WebView2，请使用[微软官方运行时](https://developer.microsoft.com/microsoft-edge/webview2/)。

程序默认把设置、队列、Cookie、播放器配置和分析缓存放在 EXE 同目录的 `data/`，默认素材目录为 `downloads/`。请放在当前用户可写的目录，避免直接放在 Program Files。通过托盘菜单退出程序；再次启动会恢复队列。

更多操作和排错请看 [使用说明](docs/USER_GUIDE.md)。

## 平台与限制

| 平台 | 支持范围 | 说明 |
| --- | --- | --- |
| YouTube | 普通单条视频 | 本地按需素材播放器；部分视频需要登录或验证 |
| B站 | 普通 BV / av 投稿、b23.tv、指定分P | 清晰度受账号权限和源格式限制，分P独立解析 |
| 抖音 | 单条视频、分享文本 | 可能要求新鲜 Cookie 或验证；优先官网播放流，排除被标记的水印下载流 |

暂不支持直播、抖音图集、B站番剧/课程、账号或合集批量下载。不会去除原视频本身已有的水印、字幕或创作者标识，也不会保证每个平台视频均可解析。

快速切割可能偏向附近关键帧；精确切割重新编码并消耗 CPU。完整视频尝试续传，片段中断后可能重下该区间。区间请求可能读取附近分段及索引，流量不一定等于输出文件大小。

手动逐帧微调准备所选区间及附近画面，每次支持 10 分钟以内的选区；保存截图只读取定位点附近的视频。自动识别镜头位于“更多工具”，读取定位时间前后 12 秒，必要时自动扩大到 30 秒；也可手动扩大到 60 秒。未找到的边界明确标为不完整。渐变、闪光、特效和字幕变化可能误判，建议看边界图再确认。终点为排除边界，之后一帧不包含在导出中。精确镜头导出采用同一缓存的实际帧索引并重新编码为 H.264/AAC。

## 开发与构建

需要 Windows、Python 3.10+、Git 和 PowerShell。项目可放在任意可写目录，以下以 E 盘为例；依赖、工具、缓存和构建产物均在项目目录内。

```powershell
Set-Location E:\OnlineVideoClipper
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\.venv\Scripts\python.exe -m pip install -r .\requirements-build.txt
.\.venv\Scripts\python.exe .\app\launcher.py
```

`setup.ps1` 从官方发布渠道下载 yt-dlp、Deno 和 FFmpeg；yt-dlp / FFmpeg 校验下载摘要。它创建 `work/`、`tools/` 和 `.venv/`。Python 环境与桌面运行依赖见 `requirements-build.txt`。

```powershell
# 自动化测试，包含本地真实 FFmpeg/yt-dlp 媒体夹具
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 先从托盘退出正在运行的便携版本，再打包
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

产物：`dist/YouTubeClipper/OnlineVideoClipper.exe` 和 `dist/OnlineVideoClipper-portable.zip`。旧目录名与 `YouTubeClipper.exe` 入口保留用于兼容；请整体移动目录。构建替换运行组件并保留本机的 `data/` 和 `downloads/`；ZIP 从干净暂存目录生成。

源码模式可用 `--browser` 打开浏览器界面，或 `--headless` 诊断后台。本机服务仅绑定 `127.0.0.1`，会话 URL 在 `data/session.json`；API 使用会话 Token 与 Host / Origin 校验。不要分享该文件或会话地址。

## 项目结构

```text
app/                 解析、队列、桌面窗口、本机服务及镜头分析
web/                 无前端构建依赖的 HTML / CSS / JavaScript 界面
tests/               自动化测试及本地媒体集成测试
scripts/             工具下载、图标生成及可选的手动/真实平台验证
docs/                用户指南、架构、发布说明
setup.ps1            下载开发所需运行组件
build.ps1            构建便携程序及干净 ZIP
requirements-build.txt  Python 桌面和构建依赖
```

`tools/`、`.venv/`、`work/`、`dist/`、`data/` 和 `downloads/` 不进入 Git。真实平台验证脚本可能联网并产生下载，仅应在明确需要时手动运行。

## 验证与参与

0.11 通过 80 项自动化测试，完成真实 B站跨缓冲段导出及桌面播放器 DOM/布局、逐帧与截图检查；历史抖音样本的镜头分析也通过。结果描述的是已测样本，不保证所有视频和系统都相同；原生窗口截图级外观检查仍有限。详细记录见 [VERIFICATION.md](VERIFICATION.md)。

欢迎提交问题和改进，见 [贡献指南](CONTRIBUTING.md)。报告问题时请移除 Cookie、Token、签名媒体地址和私人视频信息；敏感问题见 [SECURITY.md](SECURITY.md)。

## 许可证与组件

本项目源码采用 [MIT 许可证](LICENSE)。第三方运行组件遵循各自许可证，详见 [THIRD_PARTY.md](THIRD_PARTY.md)，不会因本项目许可证而改变。仅下载你有权获取和使用的内容。
