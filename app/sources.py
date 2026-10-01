"""Strict platform normalization and redirect validation."""
import re
import urllib.parse
import urllib.request

NAMES = {'youtube': 'YouTube', 'bilibili': 'B站', 'douyin': '抖音'}
HOSTS = {'youtube': {'youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be'},
         'bilibili': {'bilibili.com','www.bilibili.com','m.bilibili.com','b23.tv'},
         'douyin': {'douyin.com','www.douyin.com','v.douyin.com','www.iesdouyin.com','iesdouyin.com'}}

def parse_source(value):
    if not isinstance(value,str) or len(value)>10000:
        raise ValueError('请输入 YouTube、B站或抖音的视频链接或分享文本')
    links=re.findall(r'https?://[^\s<>"\u3000]+',value.strip())
    if len(links)!=1:
        raise ValueError('请一次粘贴一个视频链接或一段分享文本')
    url=links[0].rstrip('。，、；！）》】')
    p=urllib.parse.urlparse(url)
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443):
        raise ValueError('视频链接必须是有效的 https 链接')
    host=(p.hostname or '').lower()
    platform=next((k for k,v in HOSTS.items() if host in v),None)
    if not platform:
        raise ValueError('目前支持 YouTube、B站普通投稿和抖音单条视频')
    if host in ('b23.tv','v.douyin.com'):
        if not re.fullmatch(r'/[A-Za-z0-9_-]+/?',p.path):
            raise ValueError('分享短链接无效')
        return dict(platform=platform,url=url,id='',part=1,short=True)
    query=urllib.parse.parse_qs(p.query)
    if platform=='youtube':
        parts=p.path.strip('/').split('/')
        vid=p.path.strip('/') if host=='youtu.be' else (query.get('v',[''])[0] if p.path=='/watch' else (parts[1] if len(parts)==2 and parts[0] in ('shorts','live','embed') else ''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid): raise ValueError('链接中没有有效的视频 ID')
        url=f'https://www.youtube.com/watch?v={vid}'
        part=1
    elif platform=='bilibili':
        m=re.fullmatch(r'/video/(BV[A-Za-z0-9]{10}|av[0-9]+)/?',p.path)
        if not m: raise ValueError('请使用 B站普通投稿视频的 BV/av 链接')
        vid=m[1]
        part_text=query.get('p',['1'])[0]
        if not part_text.isdigit() or not 1<=int(part_text)<=10000: raise ValueError('B站分P编号无效')
        part=int(part_text)
        url=f'https://www.bilibili.com/video/{vid}?p={part}'
    else:
        m=re.fullmatch(r'/(?:video|share/video)/(\d+)/?',p.path)
        vid=m[1] if m else query.get('modal_id',[''])[0]
        if not re.fullmatch(r'\d{10,25}',vid): raise ValueError('请使用抖音单条视频链接，不支持直播或图集')
        part=1
        url=f'https://www.douyin.com/video/{vid}'
    return dict(platform=platform,url=url,id=vid,part=part,short=False)

class PlatformRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self,platform): self.platform=platform
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        p=urllib.parse.urlparse(newurl)
        if p.scheme!='https' or p.hostname not in HOSTS[self.platform] or p.username or p.password or p.port not in (None,443):
            raise ValueError('分享链接跳转到不受支持的地址')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def resolve_source(value,proxy=''):
    source=parse_source(value)
    if not source['short']: return source
    handlers=[PlatformRedirect(source['platform']),urllib.request.ProxyHandler({})]
    if proxy:
        if not proxy.startswith(('http://','https://')): raise ValueError('短链接展开需要 HTTP 代理，或直接使用完整视频链接')
        handlers.append(urllib.request.ProxyHandler({'https':proxy,'http':proxy}))
    opener=urllib.request.build_opener(*handlers)
    request=urllib.request.Request(source['url'],headers={'User-Agent':'Mozilla/5.0'})
    with opener.open(request,timeout=20) as response:
        result=parse_source(response.geturl())
    if result['short']: raise ValueError('短链接未能展开，请复制完整视频链接')
    return result

def platform_settings(settings,platform):
    result=dict(settings)
    if platform!='youtube':
        result['cookies']=settings.get(platform+'_cookies','')
        # Domestic sources use direct network unless explicitly configured.
        result['proxy']=settings.get(platform+'_proxy','')
        result['_direct']=True
    return result
