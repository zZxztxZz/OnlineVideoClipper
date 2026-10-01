"""Bilibili's public part list; no arbitrary URL or external parsing service."""
import json
import urllib.request
import urllib.parse


def fetch_parts(source, settings):
    ident=source['id']
    query={'bvid':ident} if ident.startswith('BV') else {'aid':ident[2:]}
    url='https://api.bilibili.com/x/player/pagelist?'+urllib.parse.urlencode(query)
    proxy=settings.get('proxy')
    if proxy and urllib.parse.urlparse(proxy).scheme.startswith('socks'):
        return [], '分P列表暂不支持 SOCKS 代理，请使用 HTTP 代理或直连。'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':proxy,'https':proxy} if proxy else {}))
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0', 'Referer':'https://www.bilibili.com/'})
        with opener.open(req,timeout=8) as response:
            info=json.loads(response.read(2_000_000))
        if info.get('code')!=0 or not isinstance(info.get('data'),list):
            return [], '分P列表读取失败，可点击重新获取。'
        pages=[dict(page=int(x['page']),title=str(x.get('part') or ('第 '+str(x['page'])+' P'))[:300],duration=x.get('duration') or 0)
            for x in info['data'] if isinstance(x,dict) and str(x.get('page','')).isdigit()][:10000]
        return pages if len(pages)>1 else [], ''
    except (OSError,ValueError,KeyError):
        return [], '分P列表读取失败，可点击重新获取。'
