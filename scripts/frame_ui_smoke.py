import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from test_scenes import SceneTests,URL
from server import LocalServer
from desktop import Desktop
import webview
SceneTests.setUpClass();fixture=SceneTests();fixture.setUp();key=fixture.e.scenes.start(URL,2.2,'90',6,mode='manual',begin=2.2,finish=3.8)['id'];fixture.e.scenes.tasks[key]['thread'].join(20);record=fixture.e.scenes.status(key)
e=fixture.e;server=LocalServer(e,ROOT/'web');desktop=Desktop(e,server,webview);server.desktop=desktop
screenshot=e.root/'capture.png'
desktop.choose_save_file=lambda initial:dict(path=str(screenshot),cancelled=False)
threading.Thread(target=server.serve_forever,daemon=True).start();result={}
def probe():
 try:
  deadline=time.monotonic()+35
  while desktop.window is None and time.monotonic()<deadline:time.sleep(.1)
  desktop.window.events.loaded.wait(30);time.sleep(.5)
  desktop.window.resize(1040,740);time.sleep(.2)
  data=json.dumps(record)
  desktop.window.evaluate_js(f'''window.shotTestDone=false;(async()=>{{
    const wait=async()=>{{for(let i=0;i<100&&$('frame-save-image').disabled;i++)await new Promise(r=>setTimeout(r,50));if($('frame-save-image').disabled)throw Error('frame readiness timeout');}};
    video={{url:{json.dumps(URL)},title:'Frame fixture',duration:6}};
    for(const id of ['fine-start','fine-end','capture-frame','download-current'])$(id).disabled=false;
    $('quality').innerHTML='<option value="90">90p</option>';$('start').value=time(2.2);$('end').value=time(3.8);
    frameCache={data};frameId='{key}';$('fine-start').click();await wait();
    const panel=document.querySelector('.cut-panel');const initial={{start:frameStart,end:frameEnd,image:$('frame-image').naturalWidth>0,panelScroll:panel.scrollHeight,panelClient:panel.clientHeight}};
    $('frame-dialog').dispatchEvent(new KeyboardEvent('keydown',{{key:'ArrowRight',bubbles:true}}));await new Promise(r=>setTimeout(r,70));await wait();
    const startStepped=frameStart;
    document.querySelector('[data-frame-role="end"]').click();await wait();$('frame-back').click();await new Promise(r=>setTimeout(r,70));await wait();const endStepped=frameEnd;
    document.querySelector('[data-frame-role="snapshot"]').click();await wait();$('frame-save-image').click();for(let i=0;i<100&&!$('frame-status').textContent.includes('已保存');i++)await new Promise(r=>setTimeout(r,50));await wait();
    const capture={{saved:$('frame-status').textContent.includes('已保存'),format:$('frame-format').value}};
    const layout={{height:innerHeight,scroll:$('frame-dialog').scrollHeight,client:$('frame-dialog').clientHeight}};
    clips=[{{start:0,end:1,name:'existing'}}];$('frame-download').click();
    const direct={{dialog:$('save-dialog').open,start:saveItems[0].shot_start_frame,end:saveItems[0].shot_end_frame,existing:clips.length}};
    $('save-dialog').close();await new Promise(r=>setTimeout(r,100));$('download-current').click();const retained=currentSelection();$('save-dialog').close();
    await new Promise(r=>setTimeout(r,100));$('capture-frame').click();await wait();const onlyScreenshot={{hidden:$('frame-clip-controls').hidden,tabsHidden:$('frame-tabs').hidden}};$('frame-dialog').close();
    window.shotTestResult={{initial,startStepped,endStepped,capture,layout,direct,retained,onlyScreenshot}};window.shotTestDone=true;
  }})().catch(e=>{{window.shotTestResult={{error:e.message}};window.shotTestDone=true;}})''')
  for _ in range(100):
   if desktop.window.evaluate_js('window.shotTestDone'):break
   time.sleep(.1)
  result.update(desktop.window.evaluate_js('window.shotTestResult') or {'error':'UI timed out'})
 except Exception as ex:result['error']=str(ex)
 finally:desktop.exit()
threading.Thread(target=probe,daemon=True).start();desktop.run();server.shutdown();e.stop();
from PIL import Image
if screenshot.exists():
 with Image.open(screenshot) as img:result['screenshot_size']=list(img.size)
fixture.download_patch.stop();fixture.temp._ignore_cleanup_errors=True;time.sleep(1);fixture.temp.cleanup();SceneTests.tearDownClass()
(ROOT/'work/frame-ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
