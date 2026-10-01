import json
import os
from pathlib import Path
import sys
import threading
import time
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from core import Engine
from server import LocalServer
from desktop import Desktop
import webview

e=Engine(ROOT/'work/v08-smoke',workers=False)
e.tools=ROOT/'tools'
e.save_settings(dict(volume=35,muted=True,quality='720',preset='original',last_output_dir=str(e.root/'recent'),window_width=1040,window_height=740))
for job in e.jobs():
    if job['state'] not in ('complete','failed','cancelled'): e.action(job['id'],'cancel')
for i,kind in enumerate(('auth','format','storage','network')):
    job=e.add_jobs(dict(url='https://www.bilibili.com/video/BV1xx411c7mD',clips=[dict(start=i,end=i+1,name=kind)]))['added'][0]
    e.patch(job,state='failed',failure_kind=kind,message='测试失败操作',metrics=json.dumps(dict(downloaded_bytes=50,total_bytes=100,speed=25,eta=2)))
job=e.add_jobs(dict(url='https://youtu.be/M7lc1UVf-VE',full=True,duration=12))['added'][0]
e.action(job,'pause')
e.save_settings(dict(last_output_dir=str(e.root/'recent')))
server=LocalServer(e,ROOT/'web')
desktop=Desktop(e,server,webview)
server.desktop=desktop
threading.Thread(target=server.serve_forever,daemon=True).start()
result={}
def probe():
    try:
        deadline=time.monotonic()+35
        while desktop.window is None and time.monotonic()<deadline: time.sleep(.1)
        desktop.window.events.loaded.wait(30)
        time.sleep(.6)
        result['restored_size']=[desktop.window.width,desktop.window.height]
        result.update(desktop.window.evaluate_js('''(()=>{
            const defaults={volume:$('preview-volume').value,muted:$('remote-video').muted,mode:$('mode').value,preset:$('preset').value};
            video={url:'https://www.bilibili.com/video/BV1bK411W797?p=1',title:'Smoke',duration:12,platform:'bilibili',part:1};
            renderParts({...video,parts:[]});const singleHidden=$('source-options').hidden;
            renderParts({...video,parts:[{page:1,title:'First part'},{page:2,title:'Second part'}]});
            const names=[...$('part-list').querySelectorAll('button')].map(b=>b.textContent);
            const previous=$('parse-form').onsubmit;
            $('parse-form').onsubmit=e=>e.preventDefault();
            $('part-list').querySelector('[data-part="2"]').click();
            const switched=$('url').value;
            $('parse-form').onsubmit=previous;
            openSave(true);const output=document.querySelector('[data-save-path]').value;$('save-dialog').close();
            const actions=[...$('jobs-list').querySelectorAll('[data-action]')].map(b=>b.dataset.action);
            return {defaults,singleHidden,names,switched,output,actions,brand:document.title};
        })()'''))
        desktop.window.resize(1100,780)
        time.sleep(.4)
    except Exception as ex:
        result['error']=str(ex)
    finally:
        desktop.exit()

threading.Thread(target=probe,daemon=True).start()
desktop.run()
result['saved_size']=[e.settings()['window_width'],e.settings()['window_height']]
for part in (1,2):
    try:
        info=e.metadata(f'https://www.bilibili.com/video/BV1bK411W797?p={part}')
        result['part'+str(part)]=dict(title=info['title'],part=info['part'],count=len(info['parts']))
    except Exception as ex: result['part'+str(part)]=dict(error=str(ex))
server.shutdown()
e.stop()
(ROOT/'work/v08-smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
