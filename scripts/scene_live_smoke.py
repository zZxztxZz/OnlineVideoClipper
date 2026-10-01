import os,sys,time,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(ROOT/'app'));os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from core import Engine
e=Engine(ROOT/'work/scene-live',workers=False);e.tools=ROOT/'tools'
result={}
try:
 url='https://www.bilibili.com/video/BV1bK411W797?p=1';info=e.metadata(url)
 key=e.scenes.start(info['url'],35,'360',info['duration'])['id']
 for _ in range(1200):
  state=e.scenes.status(key)
  if state['state']!='working':break
  time.sleep(.2)
 result={k:state[k] for k in ('state','message')}
 if state['state']=='ready':
  selected=state['shots'][state['selected_shot']];s=selected['start_index'];b=selected['end_index'];images=e.scenes.pictures(key,s,b)
  result.update(id=key,first=state['times'][0],shots=len(state['shots']),selected=selected,start=state['times'][s],end=state['times'][b] if b<len(state['times']) else state['last_end'],images=images)
except Exception as ex:result={'error':str(ex)}
finally:e.stop()
(ROOT/'work/scene-live-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
