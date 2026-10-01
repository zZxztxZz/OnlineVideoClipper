import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(ROOT/'app'))
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from core import Engine
from server import LocalServer
from desktop import Desktop
import webview
e=Engine(ROOT/'work/stream-live',workers=False);e.tools=ROOT/'tools'
info=e.metadata('https://www.bilibili.com/video/BV1bK411W797?p=1')
server=LocalServer(e,ROOT/'web');desktop=Desktop(e,server,webview);server.desktop=desktop
threading.Thread(target=server.serve_forever,daemon=True).start();result={}
def probe():
 try:
  deadline=time.monotonic()+30
  while desktop.window is None and time.monotonic()<deadline:time.sleep(.1)
  desktop.window.events.loaded.wait(30);time.sleep(.3)
  desktop.window.evaluate_js(f'''window.liveDone=false;(async()=>{{
   video={json.dumps(info,ensure_ascii=False)};$('quality').innerHTML='<option value="360">360p</option>';renderParts(video);
   const start=performance.now();await preview(video);const readyMs=performance.now()-start,src=materialVideo.src;
   await seekMaterial(35,true);await sleepUI(5000);pauseMaterial();
   const flowing={{readyMs,time:materialVideo.currentTime,srcUnchanged:src===materialVideo.src,frameCache:!!material,parts:video.parts.length,partsVisible:getComputedStyle($('source-options')).display!=='none',audio:!!streamIds.audio,audioDrift:Math.abs($('remote-audio').currentTime-materialVideo.currentTime)}};
   window.liveResult=flowing;const stepStart=performance.now();await ensureMaterialAt(materialTime);await stepMaterial(1);await sleepUI(150);flowing.stepMs=performance.now()-stepStart;flowing.frame={{time:materialTime,image:!$('cursor-image').hidden}};
   window.liveResult=flowing;window.liveDone=true;
  }})().catch(e=>{{window.liveResult={{...window.liveResult,error:e.message}};window.liveDone=true;}})''')
  for _ in range(800):
   if desktop.window.evaluate_js('window.liveDone'):break
   time.sleep(.1)
  result.update(desktop.window.evaluate_js('window.liveResult') or {'error':'timed out'})
  result['frame_details']=[t.get('detail','') for t in e.scenes.tasks.values() if t['state']=='failed']
 except Exception as ex:result['error']=str(ex)
 finally:desktop.exit()
threading.Thread(target=probe,daemon=True).start();desktop.run();server.shutdown();server.server_close();e.stop()
(ROOT/'work/stream-live-ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
