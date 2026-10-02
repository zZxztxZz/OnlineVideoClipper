"""Range streaming for extracted media; never an arbitrary-URL proxy."""
import http.cookiejar
import ipaddress
import socket
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
import os

DOMAINS=('googlevideo.com','bilivideo.com','bilivideo.cn','bilivideo.net','douyinvod.com','bytecdn.cn','byteimg.com','amemv.com','douyin.com','iesdouyin.com','pstatp.com')

def media_url(url):
    p=urllib.parse.urlparse(url)
    host=(p.hostname or '').lower()
    if p.scheme not in ('http','https') or p.username or p.password or p.port not in (None,80,443) or not any(host==d or host.endswith('.'+d) for d in DOMAINS):
        raise ValueError('当前媒体地址不支持内置预览，可手动输入时间下载')
    for info in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError('媒体地址无效')
    return urllib.parse.urlunparse(p._replace(scheme='https',netloc=host))

class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return super().redirect_request(req,fp,code,msg,headers,media_url(newurl))

def open_media(entry,range_header,tools):
    url=media_url(entry['format']['url'])
    s=entry['settings']
    if s['proxy'] and not s['proxy'].startswith(('http://','https://')):
        raise ValueError('内置预览需要 HTTP 代理，下载仍可使用 SOCKS 代理')
    ca=Path(os.environ.get('SSL_CERT_FILE') or tools/'yt-dlp'/'_internal'/'certifi'/'cacert.pem')
    # Match yt-dlp: blank YouTube proxy inherits the system environment; domestic
    # platforms explicitly opt into direct access unless configured otherwise.
    proxy_handler=urllib.request.ProxyHandler({'https':s['proxy']}) if s['proxy'] else urllib.request.ProxyHandler({}) if s.get('_direct') else urllib.request.ProxyHandler()
    handlers=[MediaRedirect(),proxy_handler,
              urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca) if ca.is_file() else None))]
    if s['cookies']:
        jar=http.cookiejar.MozillaCookieJar(s['cookies'])
        jar.load(ignore_discard=True)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    headers={k:v for k,v in entry['format'].get('http_headers',{}).items() if k.lower() in ('user-agent','referer','origin','accept','accept-language')}
    if range_header: headers['Range']=range_header
    headers['Accept-Encoding']='identity'
    return urllib.request.build_opener(*handlers).open(urllib.request.Request(url,headers=headers),timeout=20)


class ByteCache:
    BLOCK=1024*1024
    LIMIT=512*1024**2

    def __init__(self,engine):
        import threading
        self.engine=engine
        self.root=engine.data/'stream-cache';self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.locks={};self.sizes={}
        self.prune()

    def identity(self,entry):
        import hashlib,json
        s=entry['settings']
        return hashlib.sha256(json.dumps(self.engine.cache_key(entry['format']['url'],s)).encode()).hexdigest()

    def describe(self,entry):
        import re
        key=self.identity(entry)
        with self.lock:
            if key in self.sizes:return self.sizes[key]
        with open_media(entry,'bytes=0-0',self.engine.tools) as upstream:
            match=re.fullmatch(r'bytes 0-0/(\d+)',upstream.headers.get('Content-Range',''))
            if upstream.status!=206 or not match:raise ValueError('当前源不支持按需读取，请重新解析。')
            size=int(match[1])
            if size<1:raise ValueError('媒体文件大小无效')
        with self.lock:self.sizes[key]=size
        return size

    def block(self,entry,index):
        return b''.join(self.iter_block(entry,index))

    def iter_block(self,entry,index):
        import threading
        key=self.identity(entry);name=f'{key}-{index}.bin';path=self.root/name
        with self.lock:guard=self.locks.setdefault(name,threading.Lock())
        with guard:
            if path.is_file():
                os.utime(path,None);yield path.read_bytes();return
            size=self.describe(entry);start=index*self.BLOCK;end=min(size-1,start+self.BLOCK-1)
            with open_media(entry,f'bytes={start}-{end}',self.engine.tools) as upstream:
                if upstream.status!=206 or upstream.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':
                    raise ValueError('媒体服务器返回了无效的读取范围')
                temporary=path.with_suffix('.tmp');remaining=end-start+1
                try:
                    with temporary.open('wb') as output:
                        while remaining:
                            raw=upstream.read(min(64*1024,remaining))
                            if not raw:raise ValueError('媒体数据不完整，请重试。')
                            output.write(raw);remaining-=len(raw)
                            if not remaining:
                                output.close();os.replace(temporary,path);self.prune(protect=path)
                            yield raw
                finally:temporary.unlink(missing_ok=True)

    def prune(self,protect=None):
        import time
        with self.lock:
            files=sorted((p.stat().st_mtime,p.stat().st_size,p) for p in self.root.glob('*.bin'))
            total=sum(n for _,n,_ in files)
            for stamp,size,path in files:
                if path==protect:continue
                if self.locks.get(path.name) and self.locks[path.name].locked():continue
                if time.time()-stamp>86400 or total>self.LIMIT:
                    path.unlink(missing_ok=True);total-=size
