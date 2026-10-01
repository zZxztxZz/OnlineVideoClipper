from pathlib import Path
import json
import os
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'app'))
from core import Engine
ROOT=Path(__file__).resolve().parent.parent
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
e=Engine(ROOT/'work'/'speed-smoke',workers=False)
e.tools=ROOT/'tools'
url='https://www.youtube.com/watch?v=M7lc1UVf-VE'
t=time.perf_counter()
info=e.metadata(url)
report={'parse_cold_seconds':round(time.perf_counter()-t,3)}
t=time.perf_counter()
e.metadata(url)
report['parse_cached_seconds']=round(time.perf_counter()-t,3)
original=e.patch
for name,attempt in [('cached',1),('fresh',2)]:
    data=dict(url=url,title=info['title'],mode='precise',quality='360',output_dir=str(e.root/name),clips=[dict(start=3.25,end=4.25,name=name)])
    added=e.add_jobs(data)
    job=added['added'][0] if added['added'] else next(j['id'] for j in e.jobs() if j['payload']['name']==name)
    payload=next(j['payload'] for j in e.jobs() if j['id']==job)
    destination=[]
    t=time.perf_counter()
    def timed_patch(job_id,**values):
        if values.get('message')=='正在下载所选片段' and not destination:
            destination.append(round(time.perf_counter()-t,3))
        original(job_id,**values)
    e.patch=timed_patch
    e.execute(job,payload,attempt)
    e.patch=original
    row=next(j for j in e.jobs() if j['id']==job)
    report[name]=dict(state=row['state'],download_handoff_seconds=destination[0] if destination else None,total_seconds=round(time.perf_counter()-t,3),output=row['output'],detail=row['detail'])
(ROOT/'work'/'speed-smoke-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if all(report.get(n,{}).get('state')=='complete' for n in ('cached','fresh')) else 1)
