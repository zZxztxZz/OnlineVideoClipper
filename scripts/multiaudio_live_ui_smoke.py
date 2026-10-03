import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(ROOT/'app'))
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
from core import Engine
from server import LocalServer
from desktop import Desktop
import webview
e=Engine(ROOT/'work/multiaudio-live',workers=False);e.tools=ROOT/'tools'
metadataStart=time.monotonic();info=e.metadata('https://www.youtube.com/watch?v=S9STizATKjE')
metadataSeconds=time.monotonic()-metadataStart
server=LocalServer(e,ROOT/'web');desktop=Desktop(e,server,webview);server.desktop=desktop
threading.Thread(target=server.serve_forever,daemon=True).start();result={}
def probe():
 try:
  deadline=time.monotonic()+30
  while desktop.window is None and time.monotonic()<deadline:time.sleep(.1)
  desktop.window.events.loaded.wait(30);desktop.window.resize(1040,740);time.sleep(.3)
  desktop.window.evaluate_js(f'''window.liveDone=false;(async()=>{{
   video={json.dumps(info,ensure_ascii=False)};$('quality').innerHTML='<option value="1080">1080p</option>';renderParts(video);renderAudioTracks(video);
   const start=performance.now();await preview(video);if(!playerReady)throw Error($('buffer-status').textContent);const readyMs=performance.now()-start,src=materialVideo.src,original=selectedAudio();
   await seekMaterial(5,true);await sleepUI(5000);
   pauseMaterial();const timeBefore=materialVideo.currentTime;const switchStart=performance.now();$('audio-track').value='ja';await $('audio-track').onchange();
   const positionAfterSwitch=materialVideo.currentTime;await playMaterial();await sleepUI(2000);pauseMaterial();
   const switched={{readyMs,original,track:selectedAudio(),committed:committedAudio,switchMs:performance.now()-switchStart,timeBefore,positionAfterSwitch,timeAfter:materialVideo.currentTime,srcUnchanged:src===materialVideo.src,tracks:video.audio_tracks.length,audio:!!streamIds.audio,audioDrift:Math.abs($('remote-audio').currentTime-materialVideo.currentTime),videoMuted:materialVideo.muted,audioMuted:$('remote-audio').muted}};
   const cut=$('fine-start').closest('.cut-panel');switched.layout={{bodyScroll:document.body.scrollHeight,innerHeight,cutScroll:cut.scrollHeight,cutHeight:cut.clientHeight}};
   window.liveResult=switched;window.liveDone=true;
  }})().catch(e=>{{window.liveResult={{...window.liveResult,error:e.message}};window.liveDone=true;}})''')
  for _ in range(800):
   if desktop.window.evaluate_js('window.liveDone'):break
   time.sleep(.1)
  result.update(desktop.window.evaluate_js('window.liveResult') or {'error':'timed out'})
  result['metadata_seconds']=metadataSeconds
  result['frame_details']=[t.get('detail','') for t in e.scenes.tasks.values() if t['state']=='failed']
 except Exception as ex:result['error']=str(ex)
 finally:desktop.exit()
threading.Thread(target=probe,daemon=True).start();desktop.run();server.shutdown();server.server_close();e.stop()
(ROOT/'work/multiaudio-live-ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
