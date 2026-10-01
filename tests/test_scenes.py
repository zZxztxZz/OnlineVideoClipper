import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import test_app
from core import Engine
from server import LocalServer

ROOT=test_app.ROOT
URL='https://www.youtube.com/watch?v=M7lc1UVf-VE'


class SceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=tempfile.TemporaryDirectory(dir=ROOT/'work')
        cls.media=Path(cls.fixtures.name)/'shots.mp4'
        result=subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y',
            '-f','lavfi','-i','color=red:s=160x90:r=30:d=2',
            '-f','lavfi','-i','color=white:s=160x90:r=30:d=2',
            '-f','lavfi','-i','color=blue:s=160x90:r=30:d=2',
            '-f','lavfi','-i','sine=frequency=500:sample_rate=48000:duration=6',
            '-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
            '-map','[v]','-map','3:a','-c:v','libx264','-g','180','-sc_threshold','0','-c:a','aac',
            '-movflags','+faststart',str(cls.media)],capture_output=True)
        if result.returncode: raise RuntimeError(result.stderr.decode())
        class MediaHandler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                raw=cls.media.read_bytes();start=0;end=len(raw)-1
                if self.headers.get('Range'):
                    parts=self.headers['Range'][6:].split('-')
                    start=int(parts[0] or 0);end=min(end,int(parts[1])) if parts[1] else end
                    self.send_response(206)
                    self.send_header('Content-Range',f'bytes {start}-{end}/{len(raw)}')
                else:self.send_response(200)
                self.send_header('Content-Length',str(end-start+1));self.end_headers()
                try:self.wfile.write(raw[start:end+1])
                except (BrokenPipeError,ConnectionResetError):pass
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),MediaHandler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.fixtures.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'work')
        self.root=Path(self.temp.name)
        self.e=Engine(self.root,workers=False);self.e.tools=ROOT/'tools'
        fixture=dict(id='shots',title='Shots',duration=6,extractor='generic',extractor_key='Generic',webpage_url=URL,
            formats=[dict(format_id='1',url=f'http://127.0.0.1:{self.server.server_port}/shots.mp4',ext='mp4',protocol='http',width=160,height=90,vcodec='avc1',acodec='mp4a',fps=30)])
        self.e.metadata_cache[self.e.cache_key(URL,self.e.settings())]=(time.time(),{},fixture)
        self.original=self.e.scenes.download_command
        def download(*args):
            command=self.original(*args)
            for i in range(len(command)-2,0,-1):
                if command[i].startswith('ffmpeg_i:-tls_verify'): del command[i-1:i+1]
            return command
        self.download_patch=patch.object(self.e.scenes,'download_command',side_effect=download)
        self.download_patch.start()

    def tearDown(self):
        self.e.stop();self.download_patch.stop();self.temp.cleanup()

    def analyzed(self):
        key=self.e.scenes.start(URL,3,'90',6)['id']
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            state=self.e.scenes.status(key)
            if state['state']!='working': break
            time.sleep(.03)
        self.assertEqual(state['state'],'ready',state)
        return key,state

    def test_scene_cuts_find_first_frame_and_exclusive_last_frame(self):
        key,data=self.analyzed()
        selected=data['shots'][data['selected_shot']]
        self.assertEqual((selected['start_index'],selected['end_index']),(60,120))
        self.assertEqual(data['times'][60],2)
        self.assertEqual(data['times'][120],4)
        self.assertTrue(selected['left_found'] and selected['right_found'])
        pictures=self.e.scenes.pictures(key,60,120)
        for name in ('start_before','start_after','end_before','end_after'):
            self.assertTrue(self.e.scenes.asset(key,pictures[name].split('/')[-1]).is_file())

    def test_exact_export_reuses_cache_and_preserves_frame_count(self):
        key,data=self.analyzed()
        job=self.e.add_jobs(dict(url=URL,quality='90',preset='compatible',mode='fast',
            clips=[dict(name='selected',start=0,end=1,shot_cache=key,shot_start_frame=61,shot_end_frame=119)]))['added'][0]
        payload=next(j['payload'] for j in self.e.jobs() if j['id']==job)
        self.assertEqual(payload['mode'],'precise')
        self.assertAlmostEqual(payload['start'],data['times'][61],places=3)
        with patch.object(self.e,'base_command',side_effect=AssertionError('Should not re-download')):
            self.e.execute(job,payload,1)
        row=next(j for j in self.e.jobs() if j['id']==job)
        self.assertEqual(row['state'],'complete',row['detail'])
        count=json.loads(subprocess.check_output([str(self.e.tools/'ffprobe.exe'),'-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=nb_read_frames','-of','json',row['output']]))
        self.assertEqual(int(count['streams'][0]['nb_read_frames']),58)

    def test_selection_validation_and_persisted_cache(self):
        key,data=self.analyzed()
        for start,end in ((-1,2),(2,2),(1,999),(True,2)):
            with self.assertRaises(ValueError):self.e.scenes.selection(key,start,end)
        restarted=Engine(self.root,workers=False)
        self.assertEqual(restarted.scenes.record(key)['times'][60],2)
        with self.assertRaises(ValueError):self.e.scenes.asset(key,'../../queue.sqlite3')
        with self.assertRaises(ValueError):self.e.scenes.record('../bad')
        restarted.stop()

    def test_scene_api_is_session_protected_and_preview_supports_range(self):
        key,data=self.analyzed()
        server=LocalServer(self.e,ROOT/'web')
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            request=urllib.request.Request(server.origin+'/api/scene/frames',data=json.dumps(dict(id=key,start_index=60,end_index=120)).encode())
            with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(request)
            self.assertEqual(err.exception.code,403)
            request.add_header('X-Session-Token',server.token)
            with urllib.request.urlopen(request) as response:pictures=json.load(response)
            with urllib.request.urlopen(server.url+pictures['start_after']) as response:self.assertEqual(response.headers['Content-Type'],'image/jpeg')
            request=urllib.request.Request(server.url+f'shots/{key}/preview.mp4',headers={'Range':'bytes=0-31'})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status,206);self.assertEqual(len(response.read()),32)
            target=self.root/'api-frame.png'
            request=urllib.request.Request(server.origin+'/api/scene/snapshot',data=json.dumps(dict(id=key,index=60,path=str(target))).encode())
            with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(request)
            self.assertEqual(err.exception.code,403);self.assertFalse(target.exists())
            request.add_header('X-Session-Token',server.token)
            with urllib.request.urlopen(request) as response:saved=json.load(response)
            self.assertEqual(saved['time'],2);self.assertTrue(target.is_file())
        finally:server.shutdown();server.server_close()

    def test_nonzero_variable_frame_timestamps_and_boundary_images(self):
        key='a'*32
        directory=self.e.scenes.root/key;directory.mkdir()
        media=directory/'source.mkv'
        subprocess.run([str(self.e.tools/'ffmpeg.exe'),'-v','error','-y','-i',str(self.media),
            '-vf',r"select=not(eq(mod(n\,3)\,1)),setpts=PTS+20/TB",'-an','-fps_mode','passthrough',
            '-c:v','libx264','-g','180',str(media)],check=True,capture_output=True)
        task=dict(cancel=threading.Event(),process=None,center=23,duration=26,url=URL,quality='90',radius=12)
        record=self.e.scenes.analyze(task,media,20,26)
        self.assertEqual(record['times'][0],20)
        gaps={round(b-a,3) for a,b in zip(record['times'],record['times'][1:])}
        self.assertGreater(len(gaps),1)
        selected=record['shots'][record['selected_shot']]
        self.assertAlmostEqual(record['times'][selected['start_index']],22,places=2)
        (directory/'analysis.json').write_text(json.dumps(record),encoding='utf-8')
        pictures=self.e.scenes.pictures(key,selected['start_index']+2,selected['end_index']-2)
        self.assertTrue(self.e.scenes.asset(key,pictures['end_after'].split('/')[-1]).is_file())
        self.assertEqual(self.e.scenes.snap(key,0,'start'),dict(found=False,index=0))
        saved=self.e.scenes.snapshot(key,selected['start_index'],str(self.root/'nonzero.png'))
        self.assertTrue(Path(saved['path']).is_file())

    def test_cancel_releases_analysis_slot_and_removes_partial_cache(self):
        with patch.object(self.e.scenes,'download_command',return_value=[sys.executable,'-c','import time;time.sleep(30)']):
            key=self.e.scenes.start(URL,3,'90',6)['id']
            deadline=time.monotonic()+5
            while not self.e.scenes.tasks[key]['process'] and time.monotonic()<deadline:time.sleep(.02)
            self.e.scenes.cancel(key)
            self.e.scenes.tasks[key]['thread'].join(5)
        self.assertEqual(self.e.scenes.status(key)['state'],'cancelled')
        self.assertFalse(self.e.scenes.busy())
        self.assertFalse((self.e.scenes.root/key).exists())

    def test_manual_preparation_skips_scene_detection_and_snaps_only_on_request(self):
        with patch.object(self.e.scenes,'detect_cuts',wraps=self.e.scenes.detect_cuts) as detect:
            key=self.e.scenes.start(URL,2.2,'90',6,mode='manual',begin=2.2,finish=3.8)['id']
            self.e.scenes.tasks[key]['thread'].join(20)
            record=self.e.scenes.status(key)
            self.assertEqual(record['state'],'ready',record)
            self.assertFalse(record['cuts_ready'])
            detect.assert_not_called()
            self.assertEqual(self.e.scenes.snap(key,66,'start'),dict(found=True,index=60))
            self.assertEqual(self.e.scenes.snap(key,114,'end'),dict(found=True,index=120))
            self.assertEqual(detect.call_count,1)

    def test_full_resolution_screenshot_and_collision_preserve_existing_file(self):
        from PIL import Image
        key,data=self.analyzed()
        target=self.root/'downloads'/'still.png';target.parent.mkdir();target.write_bytes(b'existing')
        result=self.e.scenes.snapshot(key,60,str(target))
        self.assertEqual(target.read_bytes(),b'existing')
        self.assertEqual(Path(result['path']).name,'still (2).png')
        with Image.open(result['path']) as image:
            self.assertEqual(image.size,(160,90))
            self.assertTrue(all(c>220 for c in image.convert('RGB').getpixel((80,45))))
        jpeg=self.e.scenes.snapshot(key,120,str(target.with_suffix('.jpg')))
        with Image.open(jpeg['path']) as image:
            self.assertEqual(image.size,(160,90))
            r,g,b=image.convert('RGB').getpixel((80,45))
            self.assertGreater(b,200);self.assertLess(r,30)
        self.assertEqual(self.e.settings()['last_output_dir'],str(target.parent))
        for index,path in ((True,str(target)),(-1,str(target)),(180,str(target)),(60,'relative.png'),(60,str(target.with_suffix('.exe')))):
            with self.assertRaises(ValueError):self.e.scenes.snapshot(key,index,path)

    def test_manual_range_limit_and_snapshot_failure_cleanup(self):
        with self.assertRaises(ValueError):self.e.scenes.start(URL,3,'90',1000,mode='manual',begin=0,finish=601)
        key,data=self.analyzed();target=self.root/'downloads'/'failed.png'
        with patch.object(self.e.scenes,'run',side_effect=ValueError('media failure')):
            with self.assertRaises(ValueError):self.e.scenes.snapshot(key,60,str(target))
        self.assertFalse(target.exists())

    def test_manual_end_position_inside_last_frame_is_valid(self):
        key=self.e.scenes.start(URL,5.999,'90',6,mode='manual',begin=5,finish=6)['id']
        self.e.scenes.tasks[key]['thread'].join(20)
        result=self.e.scenes.status(key)
        self.assertEqual(result['state'],'ready',result)
        self.assertAlmostEqual(result['last_end'],6,places=2)
