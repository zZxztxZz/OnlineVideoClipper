import json
import mimetypes
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, engine, web):
        self.engine = engine
        self.web = Path(web)
        self.token = secrets.token_urlsafe(32)
        self.desktop = None
        super().__init__(('127.0.0.1', 0), Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.url = f'{self.origin}/s/{self.token}/'
        self.engine.preview_origin=self.url

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def reply(self, code, data, content_type='application/json; charset=utf-8'):
        raw = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict,list)) else data
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def trusted(self):
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            self.reply(403, dict(error='Host 无效'))
            return False
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            self.reply(403, dict(error='Origin 无效'))
            return False
        return True

    def authorized(self):
        if not self.trusted():
            return False
        if not secrets.compare_digest(self.headers.get('X-Session-Token',''), self.server.token):
            self.reply(403, dict(error='会话已失效，请从托盘重新打开界面'))
            return False
        return True

    def do_GET(self):
        if not self.trusted():
            return
        path = urlparse(self.path).path
        if path.startswith('/api/'):
            if not self.authorized():
                return
            e = self.server.engine
            if path == '/api/state':
                self.reply(200, dict(jobs=e.jobs(), settings=e.settings(), status=e.status(),
                    desktop=self.server.desktop is not None,
                    connections=self.server.desktop.connections.status() if self.server.desktop else {}))
            else:
                self.reply(404, dict(error='接口不存在'))
            return
        prefix = f'/s/{self.server.token}/'
        if not path.startswith(prefix):
            self.reply(404, b'Not found', 'text/plain')
            return
        name = path[len(prefix):] or 'index.html'
        if name.startswith('media/'):
            self.serve_media(name[6:])
            return
        if name.startswith('preview/'):
            self.serve_preview(name[8:])
            return
        if name.startswith('shots/'):
            parts=name.split('/')
            if len(parts)!=3:
                self.reply(404,dict(error='文件无效'))
                return
            try:
                file=self.server.engine.scenes.asset(parts[1],parts[2])
                if file.suffix=='.jpg':
                    self.reply(200,file.read_bytes(),'image/jpeg')
                else:
                    self.serve_file(file)
            except (ValueError,OSError): self.reply(404,dict(error='镜头缓存无效'))
            return
        if name not in ('index.html', 'app.js', 'style.css'):
            self.reply(404, b'Not found', 'text/plain')
            return
        raw = (self.server.web / name).read_bytes()
        if name == 'index.html':
            raw = raw.replace(b'__SESSION_TOKEN__', self.server.token.encode())
        self.reply(200, raw, (mimetypes.guess_type(name)[0] or 'application/octet-stream') + '; charset=utf-8')

    def serve_media(self, job_id):
        import re
        try:
            if not re.fullmatch(r'[0-9a-f]{32}',job_id):
                raise ValueError('任务无效')
            file=self.server.engine.media_path(job_id)
            self.serve_file(file)
        except (ValueError,OSError) as e:
            self.reply(404,dict(error=str(e)))

    def serve_file(self,file):
        import re
        try:
            total=file.stat().st_size
            start,end=0,total-1
            range_header=self.headers.get('Range')
            if range_header:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
                if not match or not any(match.groups()):
                    raise ValueError('Range 无效')
                if not match[1]:
                    start=max(0,total-int(match[2]))
                else:
                    start=int(match[1])
                    end=min(end,int(match[2])) if match[2] else end
                if start> end or start>=total:
                    self.send_response(416)
                    self.send_header('Content-Range',f'bytes */{total}')
                    self.end_headers()
                    return
            self.send_response(206 if range_header else 200)
            mime={'.mp4':'video/mp4','.m4a':'audio/mp4','.mkv':'video/x-matroska'}.get(file.suffix.lower(),'application/octet-stream')
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(end-start+1))
            self.send_header('Accept-Ranges','bytes')
            self.send_header('Cache-Control','private, no-store')
            if range_header:
                self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
            self.end_headers()
            with file.open('rb') as source:
                source.seek(start)
                remaining=end-start+1
                while remaining:
                    block=source.read(min(remaining,256*1024))
                    if not block: break
                    self.wfile.write(block)
                    remaining-=len(block)
        except (ConnectionResetError,BrokenPipeError,TimeoutError):
            pass
        except (ValueError,OSError) as e:
            self.reply(404,dict(error=str(e)))

    def serve_preview(self,key):
        import re,time,urllib.error
        started=False
        try:
            if not re.fullmatch(r'[0-9a-f]{32}',key):raise ValueError('预览无效')
            entry=self.server.engine.preview_source(key);cache=self.server.engine.byte_cache
            size=cache.describe(entry);header=self.headers.get('Range');begin=0;end=size-1
            if header:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
                if not match or not any(match.groups()):raise ValueError('Range 无效')
                if not match[1]:begin=max(0,size-int(match[2]))
                else:begin=int(match[1]);end=min(end,int(match[2])) if match[2] else end
                if not 0<=begin<=end<size:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
            self.send_response(206 if header else 200)
            self.send_header('Content-Type','audio/mp4' if entry['format'].get('vcodec')=='none' else 'video/mp4')
            self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-begin+1))
            if header:self.send_header('Content-Range',f'bytes {begin}-{end}/{size}')
            self.send_header('Cache-Control','private, no-store');self.end_headers();started=True
            position=begin;first=True
            browser='Chrome/' in self.headers.get('User-Agent','') and urlparse(self.path).query!='reader=1'
            while position<=end and not self.server.engine.stopping.is_set():
                if browser and not first:
                    while entry.get('flow') is False and time.monotonic()-entry.get('flow_time',0)<5:
                        if self.server.engine.stopping.wait(.1):return
                index=position//cache.BLOCK;offset=position%cache.BLOCK
                chunks=cache.iter_block(entry,index)
                try:
                    for raw in chunks:
                        if offset>=len(raw):offset-=len(raw);continue
                        part=raw[offset:offset+end-position+1];offset=0
                        if part:self.wfile.write(part);position+=len(part)
                        if position>end:break
                finally:chunks.close()
                first=False
        except (ConnectionResetError,BrokenPipeError,TimeoutError):pass
        except (ValueError,OSError,urllib.error.URLError):
            if not started:self.reply(502,dict(error='预览暂不可用，请检查网络、Cookie 或重新解析'))

    def do_POST(self):
        if not self.authorized():
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 256000:
                raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('请求格式无效')
            path = urlparse(self.path).path
            e = self.server.engine
            if path.startswith('/api/player/'):
                if e.updating: raise ValueError('正在更新，请稍后加载素材。')
                op=path.rsplit('/',1)[-1]
                if op=='stream': result=e.stream_preview(data.get('url'),data.get('quality'))
                elif op=='flow':
                    import time
                    items=data.get('items',[])
                    if not isinstance(items,list) or len(items)>2:raise ValueError('缓冲状态无效')
                    for item in items:
                        entry=e.preview_source(item.get('id'));entry['flow']=item.get('allow') is True;entry['flow_time']=time.monotonic()
                    result=dict(ok=True)
                elif op=='request': result=e.player_cache.request(data)
                elif op=='status': result=e.player_cache.status()
                elif op=='selection': result=e.player_cache.selection(data)
                else: raise ValueError('接口不存在')
            elif path.startswith('/api/scene/'):
                op=path.rsplit('/',1)[-1]
                if op=='start':
                    if e.updating: raise ValueError('正在更新，请稍后识别镜头。')
                    if data.get('mode')!='manual':raise ValueError('自动识别镜头功能已移除，请使用手动逐帧选点。')
                    result=e.scenes.start(data.get('url'),data.get('center'),data.get('quality'),data.get('duration'),data.get('radius',12),
                                          data.get('mode','scene'),data.get('begin'),data.get('finish'))
                elif op=='status': result=e.scenes.status(data.get('id'))
                elif op=='cancel': result=e.scenes.cancel(data.get('id'))
                elif op=='frames': result=e.scenes.pictures(data.get('id'),data.get('start_index'),data.get('end_index'))
                elif op=='frame': result=e.scenes.frame(data.get('id'),data.get('index'))
                elif op=='snapshot': result=e.scenes.snapshot(data.get('id'),data.get('index'),data.get('path'))
                elif op=='copy-frame':
                    if self.server.desktop is None: raise ValueError('复制图片请使用桌面版。')
                    image=e.scenes.full_frame(data.get('id'),data.get('index'))
                    result=self.server.desktop.copy_frame(image)
                else: raise ValueError('接口不存在')
            elif path in ('/api/connect/start', '/api/connect/finish', '/api/choose-cookie','/api/choose-save-file'):
                desktop = self.server.desktop
                if desktop is None:
                    raise ValueError('请在桌面版中连接平台；浏览器模式可填写 Cookie 文件路径。')
                if path == '/api/choose-save-file':
                    result=desktop.choose_save_file(data.get('initial') or '')
                elif path == '/api/choose-cookie':
                    result = desktop.choose_cookie()
                elif path.endswith('/start'):
                    result = desktop.connections.start(data.get('site'))
                else:
                    result = desktop.connections.finish(data.get('site'))
            elif path in ('/api/desktop/show', '/api/desktop/exit'):
                if self.server.desktop is None:
                    raise ValueError('当前没有桌面窗口')
                result = self.server.desktop.show() if path.endswith('/show') else self.server.desktop.exit()
            elif path == '/api/metadata':
                result = e.metadata(data.get('url'),force=data.get('force') is True)
            elif path == '/api/parts':
                from parts import fetch_parts
                from sources import parse_source,platform_settings
                source=parse_source(data.get('url'))
                if source['platform']!='bilibili': raise ValueError('平台无效')
                pages,error=fetch_parts(source,platform_settings(e.settings(),'bilibili'))
                result=dict(parts=pages,error=error)
            elif path == '/api/jobs':
                result = e.add_jobs(data)
            elif path == '/api/action':
                result = e.action(data.get('id'), data.get('action'),data.get('path'))
            elif path == '/api/settings':
                result = e.save_settings(data)
            elif path == '/api/update':
                result = e.update_engine()
            elif path == '/api/choose-directory':
                if e.native is None:
                    raise ValueError('Windows 文件夹选择不可用，可手动输入路径。')
                result=e.native.choose(data.get('initial') or e.settings()['output_dir'])
            else:
                self.reply(404, dict(error='接口不存在'))
                return
            self.reply(200, result)
        except (ValueError, TypeError, OSError) as ex:
            from core import failure_kind
            self.reply(400, dict(error=str(ex),kind=failure_kind(str(ex))))
        except Exception:
            self.reply(500, dict(error='处理失败，请检查网络或稍后重试'))
