"""Windows UI operations run in a fresh desktop/STA helper process."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

class NativeBridge:
    def __init__(self, data):
        self.data = Path(data)
        self.dialog_lock = threading.Lock()

    def request(self, operation, path):
        request_file = self.data / ('native-' + uuid.uuid4().hex + '.json')
        response_file = request_file.with_suffix('.result.json')
        request_file.write_text(json.dumps(dict(operation=operation,path=str(path)),ensure_ascii=False),encoding='utf-8')
        cmd = [sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('launcher.py'))]
        try:
            p = subprocess.run(cmd + ['--native-request',str(request_file)],capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                               timeout=None if operation=='choose' else 30)
            if not response_file.is_file():
                raise ValueError('Windows 操作未完成，请检查是否有被遮挡的系统窗口。')
            result = json.loads(response_file.read_text(encoding='utf-8'))
            if not result.get('ok'):
                raise ValueError(result.get('error') or 'Windows 操作失败')
            return result
        finally:
            request_file.unlink(missing_ok=True)
            response_file.unlink(missing_ok=True)

    def choose(self, initial):
        if not self.dialog_lock.acquire(blocking=False):
            raise ValueError('文件夹选择窗口已经打开，请先完成选择。')
        try:
            return self.request('choose',initial)
        finally:
            self.dialog_lock.release()

    def reveal(self, file):
        return self.request('reveal',file)

    def play(self, file):
        return self.request('play',file)

def shell_reveal(path):
    path = Path(path).resolve()
    if not path.exists():
        raise ValueError('文件或目录已移动，请检查保存位置。')
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('fMask',wintypes.ULONG),('hwnd',wintypes.HWND),
                  ('lpVerb',wintypes.LPCWSTR),('lpFile',wintypes.LPCWSTR),('lpParameters',wintypes.LPCWSTR),
                  ('lpDirectory',wintypes.LPCWSTR),('nShow',ctypes.c_int),('hInstApp',wintypes.HINSTANCE),
                  ('lpIDList',ctypes.c_void_p),('lpClass',wintypes.LPCWSTR),('hkeyClass',wintypes.HKEY),
                  ('dwHotKey',wintypes.DWORD),('hIcon',wintypes.HANDLE),('hProcess',wintypes.HANDLE)]
    ole=ctypes.OleDLL('ole32')
    ole.CoInitializeEx(None,2)
    try:
        shell=ctypes.WinDLL('shell32',use_last_error=True)
        shell.ShellExecuteExW.argtypes=[ctypes.POINTER(SHELLEXECUTEINFOW)]
        shell.ShellExecuteExW.restype=wintypes.BOOL
        info=SHELLEXECUTEINFOW()
        info.cbSize=ctypes.sizeof(info)
        info.fMask=0x100|0x400 # NOASYNC | FLAG_NO_UI: report errors to our caller.
        info.lpVerb='open'
        info.nShow=1 # explicitly show Explorer even if launcher was hidden
        if path.is_file():
            info.lpFile=str(Path(os.environ.get('WINDIR','C:\\Windows'))/'explorer.exe')
            info.lpParameters='/select,"'+str(path)+'"'
        else:
            info.lpFile=str(path)
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            code=ctypes.get_last_error()
            raise ValueError(f'Windows 未能打开文件夹（错误 {code}）。可复制保存路径到资源管理器。')
    finally:
        ole.CoUninitialize()
    return dict(ok=True,message='已请求 Windows 打开所在目录',path=str(path.parent if path.is_file() else path))

def run_helper(request_path):
    request_path=Path(request_path)
    response_path=request_path.with_suffix('.result.json')
    try:
        data=json.loads(request_path.read_text(encoding='utf-8'))
        if data['operation']=='choose':
            import tkinter as tk
            from tkinter import filedialog
            root=tk.Tk()
            root.withdraw()
            root.attributes('-topmost',True)
            try:
                result=filedialog.askdirectory(parent=root,title='选择本次下载的保存文件夹',initialdir=data['path'],mustexist=False)
            finally:
                root.destroy()
            answer=dict(ok=True,path=result,cancelled=not bool(result))
        elif data['operation']=='reveal':
            answer=shell_reveal(data['path'])
        elif data['operation']=='play':
            path=Path(data['path']).resolve()
            if not path.is_file():
                raise ValueError('视频文件不存在')
            os.startfile(str(path), 'open', show_cmd=1)
            answer=dict(ok=True,message='已请求系统播放器打开视频')
        elif data['operation']=='probe':
            import tkinter as tk
            root=tk.Tk()
            root.withdraw()
            root.destroy()
            answer=dict(ok=True,message='Windows 文件选择运行组件可用')
        else:
            raise ValueError('未知的 Windows 操作')
    except Exception as e:
        answer=dict(ok=False,error=str(e))
    response_path.write_text(json.dumps(answer,ensure_ascii=False),encoding='utf-8')
