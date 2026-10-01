"""Explicitly connect an owned website window; never read other browsers."""
import os
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

SITES = {'douyin': ('抖音', 'https://www.douyin.com/', ('douyin.com', 'iesdouyin.com')),
         'bilibili': ('B站', 'https://www.bilibili.com/', ('bilibili.com',))}


def netscape_cookies(cookies, site):
    domains = SITES[site][2]
    lines = ['# Netscape HTTP Cookie File', '# Saved locally by OnlineVideoClipper']
    now = time.time()
    for cookie in cookies:
        for m in cookie.values():
            domain = m['domain'].lower()
            host = domain.lstrip('.')
            if not any(host == d or host.endswith('.' + d) for d in domains):
                continue
            expires = 0
            if m['expires']:
                try:
                    expires = max(0, int(parsedate_to_datetime(m['expires']).timestamp()))
                except (ValueError, OverflowError, TypeError):
                    raise ValueError('Cookie 有效期无法读取，请重新连接。') from None
            if expires and expires <= now:
                continue
            fields = [('#HttpOnly_' if m['httponly'] else '') + domain,
                      'TRUE' if domain.startswith('.') else 'FALSE', m['path'] or '/',
                      'TRUE' if m['secure'] else 'FALSE', str(expires) if expires else '', m.key, m.value]
            if any(any(c in f for c in '\t\r\n') for f in fields):
                raise ValueError('Cookie 格式无法读取，请使用文件导入。')
            lines.append('\t'.join(fields))
    if len(lines) == 2:
        raise ValueError('尚未取得本站 Cookie。请等待网页加载，播放视频后再完成连接。')
    return '\n'.join(lines) + '\n'


class SiteConnections:
    def __init__(self, desktop):
        self.desktop = desktop
        self.lock = threading.Lock()
        self.active = {}

    def status(self):
        with self.lock:
            return {site: site in self.active for site in SITES}

    def start(self, site):
        if site not in SITES:
            raise ValueError('平台无效')
        with self.lock:
            window = self.active.get(site)
            if window:
                window.show()
                window.restore()
                return dict(ok=True)
            name, url, _ = SITES[site]
            window = self.desktop.webview.create_window(
                f'连接{name} · 浏览或登录后返回设置点击“完成连接”', url,
                width=1050, height=780, min_size=(700, 500))
            self.active[site] = window
            def closed():
                with self.lock:
                    if self.active.get(site) is window:
                        self.active.pop(site, None)
            window.events.closed += closed
        return dict(ok=True)

    def finish(self, site):
        if site not in SITES:
            raise ValueError('平台无效')
        with self.lock:
            window = self.active.get(site)
        if window is None:
            raise ValueError('连接窗口已关闭，请重新打开。')
        result, done = {}, threading.Event()
        def read():
            try:
                current = urlparse(window.get_current_url() or '')
                if current.scheme != 'https' or not any(
                    current.hostname == d or (current.hostname or '').endswith('.' + d)
                    for d in SITES[site][2]):
                    raise ValueError('请先返回该平台页面，再完成连接。')
                result['text'] = netscape_cookies(window.get_cookies(), site)
            except Exception:
                result['error'] = '未能读取本站 Cookie，请等待网页加载后重试；也可导入 Cookie 文件。'
            finally:
                done.set()
        threading.Thread(target=read, daemon=True).start()
        if not done.wait(12):
            raise ValueError('读取 Cookie 超时，请等待页面加载后重试。')
        if 'error' in result:
            raise ValueError(result['error'])
        with self.lock:
            if self.active.get(site) is not window:
                raise ValueError('连接窗口已关闭，请重新连接。')
            target = self.desktop.data / 'cookies' / (site + '.txt')
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix('.tmp')
            temp.write_text(result['text'], encoding='utf-8', newline='')
            os.replace(temp, target)
            self.desktop.engine.save_settings({site + '_cookies': str(target)})
            self.active.pop(site, None)
        window.destroy()
        self.desktop.show()
        return dict(ok=True, path=str(target), message='Cookie 已保存，请重新解析视频。')

    def close_all(self):
        with self.lock:
            windows = list(self.active.values())
            self.active.clear()
        for window in windows:
            window.destroy()
