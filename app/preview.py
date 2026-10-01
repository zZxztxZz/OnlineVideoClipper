"""Range streaming for extracted media; never an arbitrary-URL proxy."""
import http.cookiejar
import ipaddress
import socket
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
import os

DOMAINS=('bilivideo.com','bilivideo.cn','bilivideo.net','douyinvod.com','bytecdn.cn','byteimg.com','amemv.com','douyin.com','iesdouyin.com','pstatp.com')

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
    handlers=[MediaRedirect(),urllib.request.ProxyHandler({'https':s['proxy']} if s['proxy'] else {}),
              urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca) if ca.is_file() else None))]
    if s['cookies']:
        jar=http.cookiejar.MozillaCookieJar(s['cookies'])
        jar.load(ignore_discard=True)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    headers={k:v for k,v in entry['format'].get('http_headers',{}).items() if k.lower() in ('user-agent','referer','origin','accept','accept-language')}
    if range_header: headers['Range']=range_header
    headers['Accept-Encoding']='identity'
    return urllib.request.build_opener(*handlers).open(urllib.request.Request(url,headers=headers),timeout=20)
