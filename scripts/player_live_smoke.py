import json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'));os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from core import Engine
e=Engine(ROOT/'work/player-live',workers=False);e.tools=ROOT/'tools';result={}

def wait_ready(prefetch=False):
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        state=e.player_cache.status()
        if state['state']=='failed':raise ValueError(state['message'])
        if state['state']=='ready' and (not prefetch or not state['prefetch']):return state
        time.sleep(.15)
    raise ValueError('buffer timed out')

try:
    info=e.metadata('https://www.bilibili.com/video/BV1bK411W797?p=1')
    data=dict(url=info['url'],quality='360',duration=info['duration'])
    start=time.monotonic();e.player_cache.request(dict(data,time=0,first=True));first=wait_ready()
    result['first_seconds']=round(time.monotonic()-start,2);result['first_range']=first['ranges']
    end=first['record']['last_end'];e.player_cache.request(dict(data,time=end,prefetch=True,keep=[first['id']]))
    second=wait_ready(True);result['ranges']=second['ranges']
    hit=e.player_cache.request(dict(data,time=20,keep=[first['id']]))
    result['jump_reused']=hit['state']=='ready';result['windows']=len(e.player_cache.entries)
    selection=e.player_cache.selection(dict(data,start=2,end=25));result['selection']=selection
    if not selection['ready']:raise ValueError('selection gap')
    added=e.add_jobs(dict(url=info['url'],title=info['title'],quality='360',mode='precise',preset='compatible',
        clips=[dict(start=2,end=25,name='cross-window',player_segments=selection['segments'])]))['added'][0]
    with e.connect() as c:row=c.execute('SELECT * FROM jobs WHERE id=?',(added,)).fetchone()
    e.patch(added,state='downloading',attempt=1);e.execute(added,json.loads(row['payload']),1)
    job=next(j for j in e.jobs() if j['id']==added);result['export']=job['state'];result['detail']=job.get('detail','');result['output']=job.get('output','')
except Exception as ex:result['error']=str(ex)
finally:e.stop()
(ROOT/'work/player-live-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
