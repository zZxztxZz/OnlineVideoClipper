"""Owned Windows window, native dialogs and tray lifecycle."""
from pathlib import Path
import logging
import threading

from native import NativeBridge
from site_connection import SiteConnections


class Desktop(NativeBridge):
    def __init__(self, engine, server, webview):
        super().__init__(engine.data)
        self.engine, self.server, self.webview = engine, server, webview
        self.window = None
        self.icon = None
        self.exiting = False
        self.exit_lock = threading.Lock()
        self.startup_error = None
        self.connections = SiteConnections(self)

    def choose_cookie(self):
        if not self.dialog_lock.acquire(blocking=False):
            raise ValueError('文件选择窗口已经打开。')
        try:
            result = self.window.create_file_dialog(self.webview.FileDialog.OPEN,
                directory=str(self.data), file_types=('Cookie files (*.txt)', 'All files (*.*)'))
            return dict(ok=True, path=str(result[0]) if result else '', cancelled=not bool(result))
        finally:
            self.dialog_lock.release()

    def choose_save_file(self, initial):
        if not self.dialog_lock.acquire(blocking=False):
            raise ValueError('保存窗口已经打开，请先完成选择。')
        try:
            path=Path(initial)
            if not path.is_absolute() or path.suffix.lower() not in ('.mp4','.mkv','.m4a'):
                raise ValueError('保存路径无效')
            directory=path.parent
            while not directory.is_dir() and directory!=directory.parent:
                directory=directory.parent
            result=self.window.create_file_dialog(self.webview.FileDialog.SAVE,
                directory=str(directory),save_filename=path.name,
                file_types=(f'Media files (*{path.suffix})',))
            if isinstance(result,str): result=(result,)
            return dict(ok=True,path=str(result[0]) if result else '',cancelled=not bool(result))
        finally:
            self.dialog_lock.release()

    def choose(self, initial):
        if not self.dialog_lock.acquire(blocking=False):
            raise ValueError('文件夹选择窗口已经打开，请先完成选择。')
        try:
            directory = Path(initial)
            while not directory.is_dir() and directory != directory.parent:
                directory = directory.parent
            result = self.window.create_file_dialog(self.webview.FileDialog.FOLDER, directory=str(directory))
            return dict(ok=True, path=str(result[0]) if result else '', cancelled=not bool(result))
        finally:
            self.dialog_lock.release()

    def show(self, *unused):
        self.window.show()
        self.window.restore()
        return dict(ok=True)

    def closing(self):
        if self.exiting:
            return True
        self.remember_window()
        self.window.hide()
        return False

    def watch_startup(self):
        if not self.window.events.loaded.wait(35) and not self.exiting:
            self.startup_error = ('桌面窗口未能初始化。请检查 Microsoft Edge WebView2 Runtime 是否正常，'
                '或尝试重新启动程序。详细日志：' + str(self.data / 'desktop.log'))
            logging.getLogger('pywebview').error(self.startup_error)
            self.exiting = True
            self.connections.close_all()
            self.window.destroy()

    def exit(self, *unused):
        if not self.exit_lock.acquire(blocking=False):
            return dict(ok=True)
        try:
            count = sum(j['state'] in ('queued', 'retrying', 'downloading', 'processing') for j in self.engine.jobs())
            if count:
                self.show()
                if not self.window.create_confirmation_dialog('退出 OnlineVideoClipper',
                    f'还有 {count} 个任务。退出后停止下载，下次启动会重新尝试未完成任务。\n确定退出？'):
                    return dict(ok=True, cancelled=True)
            self.exiting = True
            self.remember_window()
            self.connections.close_all()
            self.window.destroy()
            return dict(ok=True)
        finally:
            self.exit_lock.release()

    def run(self):
        import pystray
        from PIL import Image, ImageDraw
        image = Image.new('RGBA', (64, 64), '#101823')
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill='#c9f368')
        draw.polygon([(26, 18), (26, 46), (46, 32)], fill='#152119')
        icon_path = self.data / 'clipper.ico'
        image.save(icon_path)
        prefs=self.engine.settings()
        self.maximized=prefs['window_maximized']
        self.normal_size=(prefs['window_width'],prefs['window_height'])
        self.window = self.webview.create_window('OnlineVideoClipper', self.server.url,
            width=prefs['window_width'], height=prefs['window_height'],maximized=prefs['window_maximized'], min_size=(860, 620), background_color='#10151b')
        self.window.events.resized += self.window_resized
        self.window.events.maximized += lambda: setattr(self,'maximized',True)
        self.window.events.restored += lambda: setattr(self,'maximized',False)
        self.window.events.closing += self.closing
        self.icon = pystray.Icon('OnlineVideoClipper', image, 'OnlineVideoClipper · 关闭窗口后继续下载',
            pystray.Menu(pystray.MenuItem('显示工作台', self.show, default=True),
                         pystray.MenuItem('退出程序', self.exit)))
        tray_thread = threading.Thread(target=self.icon.run, daemon=True)
        tray_thread.start()
        threading.Thread(target=self.watch_startup, daemon=True).start()
        try:
            self.webview.start(gui='edgechromium', private_mode=False,
                storage_path=str(self.data / 'webview'), icon=str(icon_path))
        finally:
            self.exiting = True
            self.icon.stop()
            tray_thread.join(timeout=3)
        if self.startup_error:
            raise RuntimeError(self.startup_error)

    def window_resized(self,width,height):
        if not getattr(self,'maximized',False):
            self.normal_size=(max(860,min(7680,int(width))),max(620,min(4320,int(height))))

    def remember_window(self):
        size=getattr(self,'normal_size',None)
        data=dict(window_maximized=getattr(self,'maximized',False))
        if size: data.update(window_width=size[0],window_height=size[1])
        try: self.engine.save_settings(data)
        except (ValueError,OSError): pass
