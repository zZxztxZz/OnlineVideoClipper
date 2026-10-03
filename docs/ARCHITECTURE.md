# 架构

桌面入口 `launcher.py` 管理单实例。`desktop.py` 使用 pywebview/WebView2 与系统托盘；`native.py` 负责本机保存/目录/系统播放。`site_connection.py` 使用隔离的自有窗口连接平台。

`server.py` 提供绑定 127.0.0.1 的 HTTP 服务、会话静态页面和媒体 Range 响应。界面在 `web/`，没有 Node/npm 构建依赖。`sources.py` 验证并规范化平台 URL；`parts.py` 读取分P；`preview.py` 代理允许的媒体来源并隔离凭据。

`core.py` 管理 SQLite 队列、偏好、提取缓存和受控工作进程；通过参数列表执行 yt-dlp/FFmpeg，无 shell 拼接。平台网络设置按尝试读取，任务保存独立输出参数。输出探测成功后才标记完成。

`scenes.py` 读取附近原始媒体，用 FFprobe 取得实际帧时间，FFmpeg scdet 识别画面切换。边界以开始包含/结束排除的帧索引表示；缓存代理用于预览，导出使用原缓存媒体逐帧 trim，再检查输出帧数。

运行数据只存本机。平台 Cookie、带签名媒体 URL、运行会话、播放器配置、下载内容和分析缓存均不属于源码仓库。不要把本机服务暴露给局域网或公网。

0.10 默认手动准备选区，不运行 scdet；切镜吸附显式按需检测当前缓存。单帧图片与精确剪辑共用帧时间索引，PNG/JPG 从原缓存按时间选出完整分辨率的帧，验证 Windows 文件名并独占预留目标路径，重名自动编号。


0.11 的 `player.py` 在 SceneManager 上管理按需窗口：单工作者、最新前台请求优先、同范围预取提升、取消和来源代际隔离。浏览范围按时间并集计量，最近使用回收，保护活跃播放器、边界与队列素材；磁盘采用原有安全路径回收。保存的同来源同清晰度缓存可恢复。

前端使用 HTML video 播放缓存代理，以 requestVideoFrameCallback 的实际呈现时间定位原帧索引；暂停/逐帧显示对应原素材帧图。源时间与代理时间明确换算。跨窗选区由服务端生成、再次验证包含开始/排除结束的片段列表，拒绝不连续或不同源/质量引用。FFmpeg trim + concat 从原素材一次编码，无音频输入补齐静音，队列固定引用并核对总帧数。尚未加载的选区在保存窗口按需补齐，取消不提交任务。


0.12 的普通播放器使用连续 AVC/AAC 原媒体流，单条 HTML video URL 覆盖整个视频，独立音轨同步；不再用窗口代理拼接普通播放。`preview.ByteCache` 验证域名、公网地址、TLS 和上游 Content-Range，以 1 MiB 块缓存、64 KiB 数据到达即转发，部分文件中断删除。同格式的精确帧准备通过会话内 localhost Reader URL 复用原字节；本地 HTTP 输入不添加 HTTPS 私有 TLS 参数，外网 HTTPS 输入继续校验。播放器 heartbeat 只调节浏览器请求，Reader 请求不受暂停节流影响。缓存身份包含来源、代理和 Cookie 文件时间戳。

普通播放和暂停不启动 SceneManager。首次逐帧才以 manual / preview=false 准备源帧时间，跳过 scdet 和代理编码。旧镜头缓存仍可完成历史队列导出，但自动识别与吸附 API/界面不再开放。方向键捕获处理允许时间轴焦点，并合并连续按键请求。


0.14 audio selection: audio_tracks.py groups language and description variants across bitrate formats, ranks original tracks before higher-bitrate dubs, validates track IDs, and generates language/role-constrained yt-dlp selectors. Track identity propagates through stream, player-cache session, scene record, job fingerprint, source-cache validation and sidecar. Preview registry reuses the video key when changing audio; native audio changes independently. Playback waits for both streams to be ready before starting them together. Exact extractor filesize skips the size-probe round trip, while each byte block still requires matching HTTP 206 Content-Range.
