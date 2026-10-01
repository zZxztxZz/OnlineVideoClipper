"""Exercise real WebView2 desktop lifecycle and HTML video without downloading."""
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / 'work')
sys.path.insert(0, str(ROOT / 'app'))
from core import Engine
from server import LocalServer
from desktop import Desktop
import webview

root = ROOT / 'work' / 'desktop-smoke'
engine = Engine(root, workers=False)
server = LocalServer(engine, ROOT / 'web')
desktop = Desktop(engine, server, webview)
server.desktop = desktop
engine.native = desktop
threading.Thread(target=server.serve_forever, daemon=True).start()
result = {}
finished = threading.Event()

def check():
    try:
        window = desktop.window
        if not window.events.loaded.wait(30):
            raise RuntimeError('Desktop page load timed out')
        result['renderer'] = window.evaluate_js('navigator.userAgent')
        result['title'] = window.evaluate_js('document.title')
        result['version'] = window.evaluate_js('document.querySelector("footer b").textContent')
        window.evaluate_js("document.getElementById('settings-open').click()")
        result['settings_dialog'] = window.evaluate_js("document.getElementById('settings-dialog').open")
        window.evaluate_js("document.getElementById('settings-close').click()")
        window.destroy()  # User close must cancel destruction and hide into tray.
        time.sleep(.4)
        result['close_keeps_running'] = not desktop.exiting and not window.events.closed.is_set()
        desktop.show()
        result['reopened'] = not window.events.closed.is_set()
        result['profile_on_e'] = (engine.data / 'webview').is_dir()
        assert all(result[k] for k in ('settings_dialog', 'close_keeps_running', 'reopened', 'profile_on_e'))
        assert result['version'] == '0.5'
    except Exception as e:
        result['error'] = str(e)
    finally:
        desktop.exiting = True
        desktop.window.destroy()
        finished.set()

def ready():
    threading.Thread(target=check, daemon=True).start()

try:
    # Hook creation so the smoke callback starts only after Desktop has a window.
    original = webview.start
    webview.start = lambda **kwargs: original(ready, **kwargs)
    desktop.run()
finally:
    finished.wait(3)
    engine.stop()
    server.shutdown()
    server.server_close()
    (ROOT / 'work' / 'desktop-smoke-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
if 'error' in result:
    raise SystemExit(1)
