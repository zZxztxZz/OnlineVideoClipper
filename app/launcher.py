"""Portable Windows launcher with system tray and single-instance lock."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import webbrowser
import ctypes
import logging
import urllib.request
from core import Engine
from server import LocalServer
from native import NativeBridge, run_helper

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--root')
    parser.add_argument('--native-request')
    parser.add_argument('--browser', action='store_true', help='Use browser UI for diagnostics')
    args = parser.parse_args()
    if args.native_request:
        run_helper(args.native_request)
        return
    root = Path(args.root) if args.root else (Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent)
    assets = Path(sys._MEIPASS) / 'web' if getattr(sys, 'frozen', False) else root / 'web'
    data = root / 'data'
    data.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(data)
    os.environ['DENO_DIR'] = str(data / 'deno-cache')
    lock = (data / 'instance.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        try:
            lock.seek(0)
            if lock.read(1) == b'':
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            try:
                state = json.loads((data / 'session.json').read_text())
                if state.get('desktop'):
                    token = state['url'].split('/s/')[1].split('/')[0]
                    request = urllib.request.Request(state['url'].split('/s/')[0] + '/api/desktop/show',
                        data=b'{}', headers={'X-Session-Token': token, 'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=5):
                        pass
                else:
                    webbrowser.open(state['url'])
            except Exception:
                if not args.headless:
                    ctypes.windll.user32.MessageBoxW(0, '程序正在启动，请稍后再次打开。', 'OnlineVideoClipper', 0)
            return
    engine = Engine(root)
    engine.native = NativeBridge(data)
    server = LocalServer(engine, assets)
    desktop = None
    if not args.headless and not args.browser:
        handler = logging.FileHandler(data / 'desktop.log', encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logging.getLogger('pywebview').addHandler(handler)
        import webview
        from desktop import Desktop
        desktop = Desktop(engine, server, webview)
        engine.native = desktop
    server.desktop = desktop
    statefile = data / 'session.json'
    statefile.write_text(json.dumps(dict(url=server.url, pid=os.getpid(), port=server.server_port, desktop=bool(desktop))), encoding='utf-8')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if args.headless:
            print(server.url, flush=True)
            threading.Event().wait()
        elif desktop:
            desktop.run()
        else:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new('RGBA', (64,64), '#101823')
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((6,6,58,58), radius=14, fill='#c9f368')
            draw.polygon([(26,18),(26,46),(46,32)], fill='#152119')
            def show(icon=None, item=None):
                webbrowser.open(server.url)
            def exit_app(icon, item):
                count = sum(j['state'] in ('queued','retrying','downloading','processing') for j in engine.jobs())
                if count and ctypes.windll.user32.MessageBoxW(0, f'还有 {count} 个任务。退出后停止下载，下次启动会重新尝试未完成任务。\n确定退出？', 'OnlineVideoClipper', 0x24) != 6:
                    return
                icon.stop()
            icon = pystray.Icon('OnlineVideoClipper', image, 'OnlineVideoClipper · 关闭网页仍继续下载',
                                pystray.Menu(pystray.MenuItem('打开下载界面', show, default=True), pystray.MenuItem('退出', exit_app)))
            show()
            icon.run()
    finally:
        engine.stop()
        server.shutdown()
        server.server_close()
        statefile.unlink(missing_ok=True)
        lock.close()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        if '--headless' in sys.argv:
            raise
        ctypes.windll.user32.MessageBoxW(0, str(ex), 'OnlineVideoClipper 启动失败', 0x10)
