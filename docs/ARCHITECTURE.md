# 架构

桌面入口 `launcher.py` 管理单实例。`desktop.py` 使用 pywebview/WebView2 与系统托盘；`native.py` 负责本机保存/目录/系统播放。`site_connection.py` 使用隔离的自有窗口连接平台。

`server.py` 提供绑定 127.0.0.1 的 HTTP 服务、会话静态页面和媒体 Range 响应。界面在 `web/`，没有 Node/npm 构建依赖。`sources.py` 验证并规范化平台 URL；`parts.py` 读取分P；`preview.py` 代理允许的媒体来源并隔离凭据。

`core.py` 管理 SQLite 队列、偏好、提取缓存和受控工作进程；通过参数列表执行 yt-dlp/FFmpeg，无 shell 拼接。平台网络设置按尝试读取，任务保存独立输出参数。输出探测成功后才标记完成。

`scenes.py` 读取附近原始媒体，用 FFprobe 取得实际帧时间，FFmpeg scdet 识别画面切换。边界以开始包含/结束排除的帧索引表示；缓存代理用于预览，导出使用原缓存媒体逐帧 trim，再检查输出帧数。

运行数据只存本机。平台 Cookie、带签名媒体 URL、运行会话、播放器配置、下载内容和分析缓存均不属于源码仓库。不要把本机服务暴露给局域网或公网。

0.10 默认手动准备选区，不运行 scdet；切镜吸附显式按需检测当前缓存。单帧图片与精确剪辑共用帧时间索引，PNG/JPG 从原缓存按时间选出完整分辨率的帧，验证 Windows 文件名并独占预留目标路径，重名自动编号。
