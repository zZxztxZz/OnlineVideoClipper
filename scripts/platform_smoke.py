import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
import time
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
from core import Engine
e=Engine(ROOT/'work'/'platform-smoke',workers=False)
e.tools=ROOT/'tools'
urls=sys.argv[1:] or ['https://www.bilibili.com/video/BV1xx411c7mD?p=1','https://www.douyin.com/video/6961737553342991651']
def check(url):
    start=time.time()
    try:
        data=e.metadata(url)
        result=dict(url=url,parsed=True,title=data['title'],duration=data['duration'],qualities=data['qualities'],preview=bool(data['preview']))
        # Test a one-second clip, isolated from the user's production queue.
        job=e.add_jobs(dict(url=data['url'],title=data['title'],clips=[dict(name='兼容验证',start=0,end=1)],quality='360' if any(q['height']<=360 for q in data['qualities']) else 'best',mode='precise'))['added'][0]
        payload=next(j for j in e.jobs() if j['id']==job)['payload']
        e.execute(job,payload,1)
        state=next(j for j in e.jobs() if j['id']==job)
        result.update(download=state['state'],message=state['message'],output=state['output'])
    except Exception as ex:
        result=dict(url=url,parsed=False,error=str(ex))
    result['seconds']=round(time.time()-start,2)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result
try:
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(check,urls))
    (ROOT/'work'/'platform-smoke-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
finally:e.stop()
