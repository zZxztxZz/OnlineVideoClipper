"""Continuous source playback without frame analysis or source changes."""
import json,os,sys,threading,time,subprocess,urllib.request
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent.parent;sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from test_scenes import SceneTests,URL
from server import LocalServer
from desktop import Desktop
import webview
SceneTests.setUpClass()
long_media=SceneTests.media.with_name('continuous.mp4')
subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y','-f','lavfi','-i','testsrc2=s=640x360:r=30:d=40',
 '-f','lavfi','-i','sine=frequency=400:duration=40','-c:v','libx264','-preset','veryfast','-crf','20','-g','60',
 '-c:a','aac','-movflags','+faststart',str(long_media)],check=True,capture_output=True)
SceneTests.media=long_media;fixture=SceneTests();fixture.setUp();e=fixture.e
hit=next(iter(e.metadata_cache.values()));hit[2]['duration']=40;hit[2]['formats'][0].update(width=640,height=360)
media_patch=patch('preview.open_media',side_effect=lambda entry,header,tools:urllib.request.urlopen(urllib.request.Request(entry['format']['url'],headers={'Range':header})));media_patch.start()
e.scenes.detect_cuts=lambda *args:(_ for _ in ()).throw(AssertionError('scene detection invoked'))
e.scenes.make_preview=lambda *args:(_ for _ in ()).throw(AssertionError('preview encoding invoked'))
server=LocalServer(e,ROOT/'web');desktop=Desktop(e,server,webview);server.desktop=desktop
threading.Thread(target=server.serve_forever,daemon=True).start();result={}
def probe():
 try:
  deadline=time.monotonic()+30
  while desktop.window is None and time.monotonic()<deadline:time.sleep(.1)
  desktop.window.events.loaded.wait(30);time.sleep(.3)
  desktop.window.evaluate_js(f'''window.continuousDone=false;(async()=>{{
   video={{url:{json.dumps(URL)},title:'Continuous fixture',duration:40}};$('quality').innerHTML='<option value="360">360p</option>';
   const start=performance.now();await preview(video);const src=materialVideo.src;await playMaterial();await sleepUI(14000);
   window.continuousResult={{initialMs:performance.now()-start-14000,time:materialVideo.currentTime,sourceUnchanged:src===materialVideo.src,frameCache:!!material,ended:materialVideo.ended,buffered:materialRanges}};
   pauseMaterial();$('material-seek').focus();$('material-seek').dispatchEvent(new KeyboardEvent('keydown',{{key:'ArrowRight',bubbles:true,cancelable:true}}));
   for(let i=0;i<300&&(stepBusy||!material||$('cursor-image').hidden);i++)await sleepUI(50);
   window.continuousResult.frameStep={{index:materialIndex,cache:!!material,image:!$('cursor-image').hidden,sourceUnchanged:src===materialVideo.src}};window.continuousDone=true;
  }})().catch(e=>{{window.continuousResult={{error:e.message}};window.continuousDone=true;}})''')
  for _ in range(600):
   if desktop.window.evaluate_js('window.continuousDone'):break
   time.sleep(.1)
  result.update(desktop.window.evaluate_js('window.continuousResult') or {'error':'timed out'})
  result['frame_tasks']=len(e.scenes.tasks);result['source_mb']=round(long_media.stat().st_size/1024**2,2)
  state=e.player_cache.status();result['frame_status']={k:state[k] for k in ('state','message')}
 except Exception as ex:result['error']=str(ex)
 finally:desktop.exit()
threading.Thread(target=probe,daemon=True).start();desktop.run();server.shutdown();server.server_close();e.stop();media_patch.stop();fixture.download_patch.stop();fixture.temp.cleanup();SceneTests.tearDownClass()
(ROOT/'work/continuous-ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
