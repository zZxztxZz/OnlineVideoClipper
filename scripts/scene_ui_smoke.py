import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from test_scenes import SceneTests,URL
from server import LocalServer
from desktop import Desktop
import webview
SceneTests.setUpClass();fixture=SceneTests();fixture.setUp();key,record=fixture.analyzed()
e=fixture.e;server=LocalServer(e,ROOT/'web');desktop=Desktop(e,server,webview);server.desktop=desktop
threading.Thread(target=server.serve_forever,daemon=True).start();result={}
def probe():
 try:
  deadline=time.monotonic()+35
  while desktop.window is None and time.monotonic()<deadline:time.sleep(.1)
  desktop.window.events.loaded.wait(30);time.sleep(.5)
  desktop.window.resize(1040,740);time.sleep(.2)
  data=json.dumps(record)
  desktop.window.evaluate_js(f'''window.shotTestDone=false;(async()=>{{
    video={{url:{json.dumps(URL)},title:'Scene fixture',duration:6}};
    $('quality').innerHTML='<option value="90">90p</option>';
    shot={data};shotId='{key}';$('shot-dialog').showModal();shotControls(true);selectShot(shot.selected_shot);
    await new Promise(r=>setTimeout(r,700));
    const layout={{height:innerHeight,scroll:$('shot-dialog').scrollHeight,client:$('shot-dialog').clientHeight}};
    const initial={{start:shotStart,end:shotEnd,ready:!$('shot-download').disabled,images:[...document.querySelectorAll('.shot-images img')].every(i=>i.complete&&i.naturalWidth>0)}};
    document.querySelector('[data-step="start"][data-delta="1"]').click();await new Promise(r=>setTimeout(r,400));
    const stepped={{start:shotStart,end:shotEnd}};
    clips=[{{start:0,end:1,name:'existing'}}];$('shot-download').click();
    const quick={{modal:$('save-dialog').open,frames:[saveItems[0].shot_start_frame,saveItems[0].shot_end_frame],existing:clips.length,precise:$('mode').value}};
    $('save-dialog').close();await new Promise(r=>setTimeout(r,100));$('shot-dialog').showModal();shotControls(true);selectShot(1);for(let i=0;i<50&&$('shot-add').disabled;i++)await new Promise(r=>setTimeout(r,100));$('shot-add').click();
    window.shotTestResult={{layout,initial,stepped,quick,added:clips.length,badge:$('clips-list').textContent.includes('精确镜头'),dialogClosed:!$('shot-dialog').open}};window.shotTestDone=true;
  }})().catch(e=>{{window.shotTestResult={{error:e.message}};window.shotTestDone=true;}})''')
  for _ in range(100):
   if desktop.window.evaluate_js('window.shotTestDone'):break
   time.sleep(.1)
  result.update(desktop.window.evaluate_js('window.shotTestResult') or {'error':'UI timed out'})
 except Exception as ex:result['error']=str(ex)
 finally:desktop.exit()
threading.Thread(target=probe,daemon=True).start();desktop.run();server.shutdown();e.stop();fixture.download_patch.stop();fixture.temp._ignore_cleanup_errors=True;time.sleep(1);fixture.temp.cleanup();SceneTests.tearDownClass()
(ROOT/'work/scene-ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
