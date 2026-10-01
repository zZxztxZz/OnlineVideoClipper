"""Isolated WebView2 connection probe; no personal browser/profile access."""
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'app'))
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT/'work')
from core import Engine
from desktop import Desktop
import webview

root = ROOT/'work/connection-smoke'
root.mkdir(parents=True, exist_ok=True)
engine = Engine(root, workers=False)
engine.tools = ROOT/'tools'
desktop = Desktop(engine, None, webview)
desktop.window = webview.create_window('连接功能测试', html='<p>正在验证独立平台窗口及 Cookie 保存。</p>')
result = {}

def probe():
    try:
        for site in ('bilibili','douyin'):
            started = time.monotonic()
            try:
                desktop.connections.start(site)
                window = desktop.connections.active[site]
                loaded = window.events.loaded.wait(30)
                time.sleep(3)
                r = desktop.connections.finish(site)
                result[site] = dict(loaded=loaded, saved=Path(r['path']).is_file())
                try:
                    url = 'https://www.bilibili.com/video/BV1xx411c7mD' if site=='bilibili' else 'https://www.douyin.com/video/6961737553342991651'
                    info = engine.metadata(url)
                    result[site]['parsed'] = bool(info.get('title'))
                except Exception:
                    result[site]['parsed'] = False
            except Exception as e:
                result[site] = dict(error=str(e))
            result[site]['seconds'] = round(time.monotonic()-started, 2)
            desktop.connections.close_all()
    finally:
        (ROOT/'work/connection-smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        engine.stop()
        desktop.window.destroy()

webview.start(probe, gui='edgechromium', private_mode=False, storage_path=str(root/'profile'))
print(json.dumps(result,ensure_ascii=False))
