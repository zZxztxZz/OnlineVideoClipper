import json
from pathlib import Path
import sys
import subprocess
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
from core import Engine
engine=Engine(ROOT/'work/connection-smoke',workers=False)
engine.tools=ROOT/'tools'
result={}
try:
    url='https://www.douyin.com/video/6961737553342991651'
    info=engine.metadata(url)
    added=engine.add_jobs(dict(url=info['url'], title=info['title'],
        quality='best', mode='precise',preset='compatible',
        clips=[dict(start=0,end=1,name='连接验证'+str(time.time_ns()))]))['added']
    job=added[0] if added else next(j['id'] for j in engine.jobs() if j['state']=='queued')
    queued=next(j for j in engine.jobs() if j['id']==job)
    engine.execute(job,queued['payload'],1)
    state=next(j for j in engine.jobs() if j['id']==job)
    result['state']=state['state']
    if state['state']=='complete':
        probe=subprocess.run([str(engine.tools/'ffprobe.exe'),'-v','error','-show_entries',
            'format=duration:stream=codec_type,codec_name','-of','json',state['output']],capture_output=True,text=True)
        result['media']=json.loads(probe.stdout)
    else:
        result['error']=state.get('error')
except Exception as e:
    result['error']=str(e)
finally:
    engine.stop()
    (ROOT/'work/connection-download-smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
