import json
from pathlib import Path
import sys
import subprocess
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
from core import Engine
e=Engine(ROOT/'work/connection-smoke',workers=False)
e.tools=ROOT/'tools'
result={}
try:
    info=e.metadata('https://www.douyin.com/video/6961737553342991651')
    target=e.root/'downloads'/'官网播放流完整视频.mp4'
    job=e.add_jobs(dict(url=info['url'],title=info['title'],full=True,duration=info['duration'],
        output_path=str(target),quality='best',preset='compatible',mode='fast'))['added'][0]
    p=next(j['payload'] for j in e.jobs() if j['id']==job)
    command=e.command_for(job,p,e.settings())
    result['full_has_range']='--download-sections' in command
    e.execute(job,p,1)
    state=next(j for j in e.jobs() if j['id']==job)
    result.update(state=state['state'],detail=state['detail'],output=state['output'],source_duration=info['duration'])
    if state['state']=='complete':
        raw=subprocess.check_output([str(e.tools/'ffprobe.exe'),'-v','error','-show_entries',
            'format=duration:stream=codec_name,codec_type','-of','json',state['output']])
        result['media']=json.loads(raw)
        for name,args in [('start',['-ss','2']),('end',['-sseof','-1'])]:
            subprocess.run([str(e.tools/'ffmpeg.exe'),'-v','error','-y',*args,'-i',state['output'],
                '-frames:v','1',str(ROOT/'work'/('douyin-full-'+name+'.jpg'))],capture_output=True)
finally:
    e.stop()
    (ROOT/'work/douyin-full-smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
