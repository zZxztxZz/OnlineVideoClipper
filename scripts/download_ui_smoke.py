import json
import os
from pathlib import Path
import sys
import threading

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from core import Engine
from server import LocalServer
from desktop import Desktop
import webview

engine=Engine(ROOT/'work/download-ui-smoke',workers=False)
engine.tools=ROOT/'tools'
server=LocalServer(engine,ROOT/'web')
desktop=Desktop(engine,server,webview)
server.desktop=desktop
threading.Thread(target=server.serve_forever,daemon=True).start()
desktop.window=webview.create_window('下载界面验证',server.url,width=1280,height=900)
result={}
def test():
    try:
        desktop.window.events.loaded.wait(30)
        result.update(desktop.window.evaluate_js('''(()=>{
          video={title:'界面验证视频',duration:12,url:'https://youtu.be/M7lc1UVf-VE'};
          settings.output_dir='E:\\\\YouTubeClipper\\\\work';
          openSave(true);
          const fullName=document.querySelector('[data-save-path]').value;
          const full=saveFull;
          $('save-dialog').close();
          clips=[{start:0,end:2,name:'片段一'},{start:3,end:5,name:'片段二'}];
          openSave(false);
          const batch=[...document.querySelectorAll('[data-save-path]')].map(e=>e.value);
          $('save-dialog').close();
          $('volume-controls').hidden=false;
          $('preview-volume').value=25;$('preview-volume').oninput();
          const volume=[$('remote-video').volume,$('remote-audio').volume];
          $('preview-mute').onclick();
          const muted=[$('remote-video').muted,$('remote-audio').muted];
          return {full,fullName,batch,volume,muted,controlsVisible:!$('volume-controls').hidden};
        })()'''))
    except Exception as e:
        result['error']=str(e)
    finally:
        (ROOT/'work/download-ui-smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        desktop.window.destroy()
        server.shutdown()
        engine.stop()
webview.start(test,gui='edgechromium',private_mode=False,storage_path=str(engine.data/'profile'))
print(json.dumps(result,ensure_ascii=False))
