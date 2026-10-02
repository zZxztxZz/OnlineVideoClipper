"""Owned WebView2 functional/layout smoke; no native screenshot assertion."""
import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from test_scenes import SceneTests,URL
from server import LocalServer,Handler
from desktop import Desktop
import webview
import urllib.request
from unittest.mock import patch
SceneTests.setUpClass();fixture=SceneTests();fixture.setUp();e=fixture.e
media_patch=patch('preview.open_media',side_effect=lambda entry,range_header,tools:urllib.request.urlopen(urllib.request.Request(entry['format']['url'],headers={'Range':range_header})))
media_patch.start()
if '--trace' in sys.argv:
 original_post=Handler.do_POST
 def trace_post(self):
  print('BEGIN',self.path,flush=True);original_post(self);print('END',self.path,flush=True)
 Handler.do_POST=trace_post
server=LocalServer(e,ROOT/'web');desktop=Desktop(e,server,webview);server.desktop=desktop
screenshot=e.root/'capture.png'
desktop.choose_save_file=lambda initial:dict(path=str(screenshot),cancelled=False)
copied_frames=[]
desktop.copy_frame=lambda path:copied_frames.append(str(path)) or dict(ok=True,width=160,height=90)
threading.Thread(target=server.serve_forever,daemon=True).start();result={}

def probe():
 try:
  deadline=time.monotonic()+35
  while desktop.window is None and time.monotonic()<deadline:time.sleep(.1)
  desktop.window.events.loaded.wait(30);time.sleep(.5)
  desktop.window.resize(1040,740);time.sleep(.2)
  desktop.window.evaluate_js(f'''window.playerTestDone=false;(async()=>{{
    const wait=async(picture=false)=>{{for(let i=0;i<300&&($('capture-frame').disabled||(picture&&$('cursor-image').hidden));i++)await sleepUI(50);if($('capture-frame').disabled)throw Error('frame readiness timeout');}};
    video={{url:{json.dumps(URL)},title:'Unified player fixture',duration:6}};
    $('quality').innerHTML='<option value="90">90p</option>';$('quality').value='90';$('start').value=time(2.2);$('end').value=time(3.8);
    for(const id of ['fine-start','fine-end','capture-frame','download-current'])$(id).disabled=false;
    await preview(video);await wait();const initial={{src:materialVideo.src,frameCache:!!material,iframe:!!document.querySelector('iframe'),dialog:!!$('frame-dialog'),automatic:!!$('select-shot')}};
    $('material-seek').onpointerdown();$('material-seek').value=2.2;await $('material-seek').onchange();await wait();const reused=materialVideo.src===initial.src;
    await $('fine-start').onclick();await ensureMaterialAt(2.2);await wait();const startBefore=materialIndex;await stepMaterial(1);await sleepUI(100);await wait(true);const startAfter=materialIndex,startTime=$('start').value;
    await $('fine-end').onclick();await ensureMaterialAt(3.799);await wait();const endBefore=materialIndex;await stepMaterial(-1);await sleepUI(100);await wait(true);const endAfter=materialIndex,endTime=$('end').value;
    const selected=await ensureExport(range());const noExtraBuffer=materialVideo.src===initial.src;
    $('download-current').onclick();await $('save-form').onsubmit({{preventDefault(){{}}}});const queued=jobs.find(j=>j.payload.player_segments);if(!queued)throw Error('exact queue submission failed');showView('studio');
    await seekMaterial(4.2,false);await wait();materialRole='cursor';await $('capture-frame').onclick();await $('copy-frame').onclick();
    const hoverRect=$('material-seek').getBoundingClientRect();showTimelineHover({{clientX:hoverRect.left+hoverRect.width/2}});const hover={{text:$('timeline-hover').textContent,visible:!$('timeline-hover').hidden}};$('material-seek').dispatchEvent(new PointerEvent('pointerleave'));const hoverHidden=$('timeline-hover').hidden;const clockGrouped=$('current-time').parentElement===$('video-duration').parentElement;const displaySize=[$('cursor-image').naturalWidth,$('cursor-image').naturalHeight];
    const cut=$('.unused')||$('fine-start').closest('.cut-panel'),panel=$('remote-video').closest('.preview-panel');
    const layout={{innerHeight:innerHeight,cutHeight:cut.clientHeight,cutScroll:cut.scrollHeight,playerHeight:panel.clientHeight,imageWidth:$('cursor-image').clientWidth,bodyScroll:document.body.scrollHeight}};
    await playMaterial();await sleepUI(300);pauseMaterial();await wait();const playing={{time:materialTime,paused:materialVideo.paused}};
    await ensureMaterialAt(materialTime);const keyboardBefore=materialIndex;$('material-seek').focus();$('material-seek').dispatchEvent(new KeyboardEvent('keydown',{{key:'ArrowRight',bubbles:true,cancelable:true}}));await sleepUI(150);await wait(true);const keyboardAfter=materialIndex;
    renderParts({{part:1,parts:[{{page:1,title:'第一部分'}},{{page:2,title:'第二部分'}}]}});const partsVisible=getComputedStyle($('source-options')).display!=='none';const submit=$('parse-form').requestSubmit;let switched=false;$('parse-form').requestSubmit=()=>{{switched=true;}};$('part-list').querySelector('[data-part="2"]').click();$('parse-form').requestSubmit=submit;
    window.playerTestResult={{hover,hoverHidden,clockGrouped,displaySize,initial,reused,startBefore,startAfter,startTime,endBefore,endAfter,endTime,selected,noExtraBuffer,queued:{{mode:queued.payload.mode,segments:queued.payload.player_segments}},layout,playing,keyboardBefore,keyboardAfter,partsVisible,switched}};window.playerTestDone=true;
  }})().catch(e=>{{window.playerTestResult={{error:e.message,stack:e.stack}};window.playerTestDone=true;}})''')
  for _ in range(400):
   if desktop.window.evaluate_js('window.playerTestDone'):break
   time.sleep(.1)
  result.update(desktop.window.evaluate_js('window.playerTestResult') or {'error':'UI timed out'})
 except Exception as ex:result['error']=str(ex)
 finally:
  for job in e.jobs():
   if job['state']=='queued':e.action(job['id'],'cancel')
  desktop.exit()
threading.Thread(target=probe,daemon=True).start();desktop.run();server.shutdown();server.server_close();e.stop()
from PIL import Image
result['copied_frames']=len(copied_frames)
if screenshot.exists():
 with Image.open(screenshot) as img:result['screenshot_size']=list(img.size)
media_patch.stop();fixture.download_patch.stop();fixture.temp._ignore_cleanup_errors=True;time.sleep(.5);fixture.temp.cleanup();SceneTests.tearDownClass()
(ROOT/'work/player-ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
