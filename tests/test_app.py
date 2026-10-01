import io
import json
import os
import re
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'app'))
from core import Engine, video_url, seconds, classify_error
from server import LocalServer

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'work')
        self.root = Path(self.temp.name)
        self.e = Engine(self.root, workers=False)

    def test_database_connections_close_and_transactions_rollback(self):
        with self.e.connect() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES ('connection-test','1')")
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute('SELECT 1')
        with self.assertRaises(ValueError):
            with self.e.connect() as transaction:
                transaction.execute("UPDATE settings SET value='2' WHERE key='connection-test'")
                raise ValueError('rollback')
        with self.e.connect() as check:
            self.assertEqual(check.execute("SELECT value FROM settings WHERE key='connection-test'").fetchone()[0],'1')

    def tearDown(self):
        self.e.stop()
        self.temp.cleanup()

    def payload(self, **kwargs):
        return dict(url='https://youtu.be/M7lc1UVf-VE',title='测试视频',clips=[dict(start=3.25,end=7.75,name='测试')],**kwargs)

    def test_url_validation(self):
        for url in ('https://youtu.be/M7lc1UVf-VE?t=3','https://www.youtube.com/watch?v=M7lc1UVf-VE&list=abc','https://youtube.com/shorts/M7lc1UVf-VE'):
            self.assertEqual(video_url(url)[1], 'M7lc1UVf-VE')

    def test_full_and_named_download_validation(self):
        target=self.root/'输出'/ '自定义视频.mp4'
        job=self.e.add_jobs(dict(url='https://youtu.be/M7lc1UVf-VE',full=True,
            duration=12,output_path=str(target),preset='compatible',mode='precise'))['added'][0]
        p=next(j['payload'] for j in self.e.jobs() if j['id']==job)
        self.assertTrue(p['full']);self.assertEqual(p['filename'],target.name)
        self.e.tools=ROOT/'tools'
        command=self.e.command_for(job,p,self.e.settings())
        self.assertNotIn('--download-sections',command)
        self.assertNotIn('--force-keyframes-at-cuts',command)
        for name in ('CON.mp4','bad?.mp4','wrong.mkv'):
            with self.assertRaises(ValueError):
                self.e.add_jobs(dict(url=p['url'],full=True,output_path=str(target.parent/name),preset='compatible'))
        for url in ('https://youtube.com.evil.test/watch?v=M7lc1UVf-VE','http://localhost/a','https://youtu.be/a','https://user@youtu.be/M7lc1UVf-VE','https://youtu.be:8080/M7lc1UVf-VE'):
            with self.assertRaises(ValueError):
                video_url(url)

    def test_timestamps(self):
        self.assertEqual(seconds('01:02:03.250'),3723.25)
        for value in ('nan','inf','-1','1:99','1:-2',None):
            with self.assertRaises(ValueError):
                seconds(value)

    def test_atomic_batch_and_duplicates(self):
        p=self.payload()
        p['clips'].append(dict(start=9,end=2))
        with self.assertRaises(ValueError):
            self.e.add_jobs(p)
        self.assertEqual(self.e.jobs(),[])
        first=self.e.add_jobs(self.payload())
        second=self.e.add_jobs(self.payload())
        self.assertEqual(len(first['added']),1)
        self.assertEqual(second['duplicates'],1)

    def test_persistence_restart_and_cancel_retry(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        self.e.patch(job,state='downloading',attempt=1)
        recovered=Engine(self.root,workers=False)
        self.assertEqual(recovered.jobs()[0]['state'],'queued')
        recovered.action(job,'cancel')
        self.assertEqual(recovered.jobs()[0]['state'],'cancelled')
        recovered.action(job,'retry')
        self.assertEqual(recovered.jobs()[0]['attempt'],0)
        recovered.stop()

    def test_output_snapshot(self):
        self.e.save_settings(dict(output_dir=str(self.root/'first')))
        self.e.add_jobs(self.payload())
        self.e.save_settings(dict(output_dir=str(self.root/'second')))
        self.assertEqual(self.e.jobs()[0]['payload']['output_dir'],str(self.root/'first'))

    def test_retry_is_bounded_and_permanent_failure_stops(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        payload=self.e.jobs()[0]['payload']
        with patch.object(self.e,'command_for',return_value=['fake']),patch('core.subprocess.Popen',side_effect=OSError('Connection timed out')):
            self.e.execute(job,payload,1)
            self.assertEqual(self.e.jobs()[0]['state'],'retrying')
            self.e.execute(job,payload,4)
            self.assertEqual(self.e.jobs()[0]['state'],'failed')
        with patch.object(self.e,'command_for',return_value=['fake']),patch('core.subprocess.Popen',side_effect=OSError('Private video: Sign in')):
            self.e.execute(job,payload,1)
            self.assertEqual(self.e.jobs()[0]['state'],'failed')

    def test_cancel_does_not_start_process(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        self.e.action(job,'cancel')
        with patch.object(self.e,'command_for',return_value=['fake']),patch('core.subprocess.Popen') as spawn:
            self.e.execute(job,self.e.jobs()[0]['payload'],1)
            spawn.assert_not_called()

    def test_range_command_and_no_shell(self):
        self.e.tools=ROOT/'tools'
        job=self.e.add_jobs(self.payload(mode='precise'))['added'][0]
        c=self.e.command_for(job,self.e.jobs()[0]['payload'],self.e.settings())
        self.assertEqual(c[c.index('--download-sections')+1],'*3.25-7.75')
        self.assertIn('--force-keyframes-at-cuts',c)
        self.assertTrue(any('-c:v libx264' in arg for arg in c))
        self.assertEqual(c[-2],'--')

    def test_log_redaction(self):
        text=self.e.redact('error https://user:secret@proxy.test/a?token=abc')
        self.assertNotIn('secret',text)
        self.assertNotIn('token',text)

    def test_per_download_directory_does_not_change_default(self):
        default=self.e.settings()['output_dir']
        target=self.root/'chosen'
        p=self.payload(output_dir=str(target))
        self.e.add_jobs(p)
        self.assertEqual(self.e.jobs()[0]['payload']['output_dir'],str(target))
        self.assertEqual(self.e.settings()['output_dir'],default)
        with self.assertRaises(ValueError):self.e.add_jobs(self.payload(output_dir='relative'))

    def test_cache_reselects_quality_and_expires(self):
        url='https://www.youtube.com/watch?v=M7lc1UVf-VE'
        s=self.e.settings()
        info=dict(id='x',formats=[dict(url='https://media.test/a?expire='+str(int(time.time()+900)),height=360)],
                  requested_formats=[dict(height=2160)],height=2160,url='https://media.test/selected')
        key=self.e.cache_key(url,s)
        self.e.metadata_cache[key]=(time.time(),dict(id='x'),info)
        cache=self.e.cached_download_info(url,s)
        self.assertNotIn('requested_formats',cache)
        self.assertNotIn('height',cache)
        self.assertEqual(cache['formats'][0]['height'],360)
        self.e.metadata_cache[key]=(time.time()-301,{},info)
        self.assertIsNone(self.e.cached_download_info(url,s))
        self.e.metadata_cache[key]=(time.time(),{},dict(formats=[dict(url='https://media.test/a?expire=1')]))
        self.assertIsNone(self.e.cached_download_info(url,s))

    def test_play_validates_file_and_folder_uses_native_bridge(self):
        from unittest.mock import Mock
        job=self.e.add_jobs(self.payload())['added'][0]
        file=self.root/'video.mp4'
        file.write_bytes(b'media')
        self.e.patch(job,state='complete',output=str(file))
        self.assertEqual(self.e.action(job,'open')['media'],job)
        self.e.native=Mock()
        self.e.native.reveal.return_value=dict(ok=True)
        self.e.action(job,'folder')
        self.e.native.reveal.assert_called_once_with(file)
        self.e.action(job,'system-play')
        self.e.native.play.assert_called_once_with(file)
        file.unlink()
        with self.assertRaises(ValueError):self.e.action(job,'open')
        with self.assertRaises(ValueError):self.e.action(job,'system-play')

    def test_rejected_cache_refreshes_without_backoff(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        payload=self.e.jobs()[0]['payload']
        (self.e.data/'jobs'/job).mkdir(parents=True)
        key=self.e.cache_key(payload['url'],self.e.settings())
        self.e.metadata_cache[key]=(time.time(),{},dict(id='x',formats=[]))
        with patch.object(self.e,'command_for',return_value=['fake','--',payload['url']]),patch('core.subprocess.Popen',side_effect=[OSError('HTTP error 403 Forbidden'),OSError('Sign in required')]) as spawn:
            self.e.execute(job,payload,1)
            self.assertEqual(spawn.call_count,2)
            self.assertIn('--load-info-json',spawn.call_args_list[0].args[0])
            self.assertNotIn('--load-info-json',spawn.call_args_list[1].args[0])
            self.assertEqual(self.e.jobs()[0]['state'],'failed')

class HTTPTests(unittest.TestCase):
    payload = CoreTests.payload
    def setUp(self):
        CoreTests.setUp(self)
        self.server=LocalServer(self.e,ROOT/'web')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        CoreTests.tearDown(self)

    def request(self,path,data=None,token=True,origin=None,host=None):
        headers={'X-Session-Token':self.server.token} if token else {}
        if origin: headers['Origin']=origin
        if host: headers['Host']=host
        request=urllib.request.Request(self.server.origin+path, data=json.dumps(data).encode() if data is not None else None,headers=headers)
        return urllib.request.urlopen(request,timeout=3)

    def test_api_access_and_origin(self):
        for kwargs in (dict(token=False),dict(origin='https://evil.example'),dict(host='evil.example')):
            with self.assertRaises(urllib.error.HTTPError) as ex:
                self.request('/api/state',**kwargs)
            self.assertEqual(ex.exception.code,403)
        with self.request('/api/jobs',self.payload()) as r:
            self.assertEqual(len(json.load(r)['added']),1)
        with self.request('/api/state') as r:
            self.assertEqual(len(json.load(r)['jobs']),1)

    def test_assets_and_traversal(self):
        with self.request('/s/'+self.server.token+'/') as r:
            self.assertIn(self.server.token.encode(),r.read())
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/s/'+self.server.token+'/../../app/core.py')

    def test_desktop_reopen_requires_session_and_delegates(self):
        from unittest.mock import Mock
        self.server.desktop=Mock()
        self.server.desktop.show.return_value=dict(ok=True)
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/desktop/show',{},token=False)
        self.server.desktop.show.assert_not_called()
        with self.request('/api/desktop/show',{}) as response:
            self.assertTrue(json.load(response)['ok'])
        self.server.desktop.show.assert_called_once()

    def test_platform_connection_routes_require_session_and_desktop(self):
        from unittest.mock import Mock
        with self.assertRaises(urllib.error.HTTPError) as ex:
            self.request('/api/connect/start', {'site':'douyin'})
        self.assertEqual(ex.exception.code, 400)
        self.server.desktop = Mock()
        self.server.desktop.connections.start.return_value = dict(ok=True)
        self.server.desktop.connections.finish.return_value = dict(ok=True, path='local.txt')
        self.server.desktop.connections.status.return_value = dict(douyin=True, bilibili=False)
        for route in ('start','finish'):
            with self.assertRaises(urllib.error.HTTPError):
                self.request('/api/connect/'+route, {'site':'douyin'}, token=False)
            with self.request('/api/connect/'+route, {'site':'douyin'}) as r:
                self.assertTrue(json.load(r)['ok'])
        self.server.desktop.connections.start.assert_called_once_with('douyin')
        self.server.desktop.connections.finish.assert_called_once_with('douyin')
        with self.request('/api/state') as r:
            self.assertTrue(json.load(r)['connections']['douyin'])

    def test_media_ranges_and_uncompleted_rejection(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        media_path='/s/'+self.server.token+'/media/'+job
        with self.assertRaises(urllib.error.HTTPError):self.request(media_path)
        file=self.root/'clip.mp4'
        file.write_bytes(b'0123456789')
        self.e.patch(job,state='complete',output=str(file))
        req=urllib.request.Request(self.server.origin+media_path,headers={'Range':'bytes=2-5'})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status,206)
            self.assertEqual(r.read(),b'2345')
            self.assertEqual(r.headers['Content-Range'],'bytes 2-5/10')
        req=urllib.request.Request(self.server.origin+media_path,headers={'Range':'bytes=-3'})
        with urllib.request.urlopen(req) as r:self.assertEqual(r.read(),b'789')
        req=urllib.request.Request(self.server.origin+media_path,headers={'Range':'bytes=20-'})
        with self.assertRaises(urllib.error.HTTPError) as ex:urllib.request.urlopen(req)
        self.assertEqual(ex.exception.code,416)

class RealMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(dir=ROOT/'work')
        cls.root=Path(cls.temp.name)
        cls.e=Engine(cls.root,workers=False)
        cls.e.tools=ROOT/'tools'
        cls.media=cls.root/'fixture.mp4'
        cls.slow=False
        cls.range_requests=[]
        r=subprocess.run([str(ROOT/'tools'/'ffmpeg.exe'),'-hide_banner','-loglevel','error','-y',
                          '-f','lavfi','-i','testsrc2=size=320x180:rate=30','-f','lavfi','-i','sine=frequency=880:sample_rate=48000',
                          '-t','12','-c:v','libx264','-g','60','-c:a','aac','-movflags','+faststart',str(cls.media)],capture_output=True)
        if r.returncode: raise RuntimeError(r.stderr.decode())
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class MediaHandler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                raw=cls.media.read_bytes()
                start,end=0,len(raw)-1
                range_header=self.headers.get('Range')
                if range_header:
                    cls.range_requests.append(range_header)
                    parts=range_header.removeprefix('bytes=').split('-')
                    start=int(parts[0] or 0)
                    end=min(end,int(parts[1])) if parts[1] else end
                    self.send_response(206)
                    self.send_header('Content-Range',f'bytes {start}-{end}/{len(raw)}')
                else: self.send_response(200)
                self.send_header('Accept-Ranges','bytes')
                self.send_header('Content-Type','video/mp4')
                self.send_header('Content-Length',str(end-start+1))
                self.end_headers()
                try:
                    if cls.slow:
                        for offset in range(start,end+1,8192):
                            self.wfile.write(raw[offset:min(end+1,offset+8192)])
                            self.wfile.flush()
                            time.sleep(.03)
                    else: self.wfile.write(raw[start:end+1])
                except (ConnectionResetError,BrokenPipeError):pass
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),MediaHandler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.fixture=cls.root/'info.json'
        cls.fixture.write_text(json.dumps(dict(id='fixture',title='Fixture',duration=12,extractor='generic',extractor_key='Generic',
              webpage_url=f'http://127.0.0.1:{cls.server.server_port}/fixture.mp4',formats=[dict(format_id='1',url=f'http://127.0.0.1:{cls.server.server_port}/fixture.mp4',ext='mp4',protocol='http',height=180,width=320,vcodec='avc1',acodec='mp4a',fps=30)])),encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.e.stop()
        cls.temp.cleanup()

    def exercise(self,mode,preset='compatible',quality='best',full=False,filename=None):
        selection=dict(full=True,duration=12,output_path=str(filename) if filename else '') if full else dict(clips=[dict(start=3.25,end=7.75,name=mode+quality,output_path=str(filename) if filename else '')])
        job=self.e.add_jobs(dict(url='https://youtu.be/M7lc1UVf-VE',title='Fixture',mode=mode,preset=preset,quality=quality,**selection))['added'][0]
        payload=next(j['payload'] for j in self.e.jobs() if j['id']==job)
        original=self.e.command_for
        def fixture_command(*args):
            cmd=original(*args)
            # Production inputs are HTTPS. This test fixture uses local HTTP,
            # whose demuxer rejects TLS-only ca_file options.
            for i in range(len(cmd)-2,0,-1):
                if cmd[i].startswith('ffmpeg_i:-tls_verify'):
                    del cmd[i-1:i+1]
            return cmd[:-2]+['--load-info-json',str(self.fixture)]
        self.e.patch(job,state='downloading',attempt=1)
        with patch.object(self.e,'command_for',side_effect=fixture_command):
            self.e.execute(job,payload,1)
        row=next(j for j in self.e.jobs() if j['id']==job)
        self.assertEqual(row['state'],'complete',row['detail'])
        final=Path(row['output'])
        self.assertTrue(final.is_file())
        self.assertTrue(final.with_suffix(final.suffix+'.source.json').is_file())
        probe=json.loads(subprocess.check_output([str(ROOT/'tools'/'ffprobe.exe'),'-v','error','-show_entries','format=duration:stream=codec_name,codec_type','-of','json',str(final)]))
        duration=float(probe['format']['duration'])
        if full: self.assertAlmostEqual(duration,12,delta=.15)
        elif mode=='precise': self.assertAlmostEqual(duration,4.5,delta=.15)
        else: self.assertLess(abs(duration-4.5),2.5)
        if quality!='audio':
            self.assertTrue(any(s['codec_type']=='video' for s in probe['streams']))
            self.assertTrue(any(s['codec_type']=='audio' for s in probe['streams']))
        else:
            self.assertFalse(any(s['codec_type']=='video' for s in probe['streams']))
        return final

    def test_fast_mp4(self):self.assertEqual(self.exercise('fast').suffix,'.mp4')
    def test_precise_mp4(self):self.assertEqual(self.exercise('precise').suffix,'.mp4')
    def test_fast_original(self):self.assertEqual(self.exercise('fast','original').suffix,'.mkv')
    def test_precise_audio(self):self.assertEqual(self.exercise('precise',quality='audio').suffix,'.m4a')
    def test_full_named_file_does_not_overwrite_existing(self):
        target=self.root/'downloads'/'完整自定义.mp4'
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b'keep existing file')
        final=self.exercise('precise',full=True,filename=target)
        self.assertEqual(final.name,'完整自定义 (2).mp4')
        self.assertEqual(target.read_bytes(),b'keep existing file')

    def test_full_pause_resume_uses_http_range_and_retains_partial_file(self):
        self.__class__.slow=True
        self.range_requests.clear()
        target=self.root/'downloads'/'resume.mp4'
        job=self.e.add_jobs(dict(url='https://youtu.be/M7lc1UVf-VE',full=True,duration=12,
            output_path=str(target),quality='180',mode='fast',preset='compatible'))['added'][0]
        payload=next(j['payload'] for j in self.e.jobs() if j['id']==job)
        original=self.e.command_for
        def command(*args):
            cmd=original(*args)
            for i in range(len(cmd)-2,0,-1):
                if cmd[i].startswith('ffmpeg_i:-tls_verify'): del cmd[i-1:i+1]
            return cmd[:-2]+['--load-info-json',str(self.fixture)]
        thread=None
        try:
            with patch.object(self.e,'command_for',side_effect=command):
                thread=threading.Thread(target=self.e.execute,args=(job,payload,1))
                thread.start()
                deadline=time.time()+10
                work=self.e.data/'jobs'/job
                while time.time()<deadline:
                    partial=list(work.rglob('*.part'))
                    if partial and partial[0].stat().st_size>8192: break
                    time.sleep(.04)
                self.assertTrue(partial,str(next(j for j in self.e.jobs() if j['id']==job)))
                self.e.action(job,'pause')
                thread.join(10)
                self.assertFalse(thread.is_alive())
                self.assertEqual(next(j for j in self.e.jobs() if j['id']==job)['state'],'paused')
                self.assertTrue(partial[0].is_file())
                self.__class__.slow=False
                self.e.action(job,'resume')
                self.e.execute(job,payload,1)
                state=next(j for j in self.e.jobs() if j['id']==job)
                self.assertEqual(state['state'],'complete',state['detail'])
                self.assertTrue(any(re.fullmatch(r'bytes=[1-9]\d*-',r) for r in self.range_requests),self.range_requests)
        finally:
            self.__class__.slow=False
            if thread and thread.is_alive():
                self.e.action(job,'cancel')
                thread.join(10)
    def test_cached_media_pipeline(self):
        url='https://www.youtube.com/watch?v=M7lc1UVf-VE'
        key=self.e.cache_key(url,self.e.settings())
        self.e.metadata_cache[key]=(time.time(),{},json.loads(self.fixture.read_text(encoding='utf-8')))
        self.assertEqual(self.exercise('fast',quality='180').suffix,'.mp4')

if __name__=='__main__':unittest.main()
