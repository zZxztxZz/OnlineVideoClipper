import json
import subprocess
import time
import uuid
from unittest.mock import patch
import test_scenes as fixture_module
import unittest
URL=fixture_module.URL
ROOT=fixture_module.ROOT
from player import PlayerCache


class PlayerTests(unittest.TestCase):
    setUpClass=classmethod(fixture_module.SceneTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture_module.SceneTests.tearDownClass.__func__)
    setUp=fixture_module.SceneTests.setUp
    tearDown=fixture_module.SceneTests.tearDown
    def ready(self):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            state=self.e.player_cache.status()
            if state['state']!='working':break
            time.sleep(.03)
        self.assertEqual(state['state'],'ready',state)
        return state

    def test_windows_first_jump_prefetch_end(self):
        self.assertEqual(PlayerCache.window(0,200,first=True),(0,8))
        self.assertEqual(PlayerCache.window(100,200),(97,112))
        self.assertEqual(PlayerCache.window(100,200,prefetch=True),(100,124))
        self.assertEqual(PlayerCache.window(198,200),(195,200))

    def test_same_source_reuses_playback_for_frames_and_export(self):
        cache=self.e.player_cache
        cache.request(dict(url=URL,quality='90',duration=6,time=0,first=True))
        state=self.ready();key=state['id']
        with patch.object(self.e.scenes,'download_command',side_effect=AssertionError('redownload')):
            reused=cache.request(dict(url=URL,quality='90',duration=6,time=3.1))
            self.assertEqual(reused['id'],key)
            frame=self.e.scenes.frame(key,93);self.assertEqual(frame['time'],3.1)
            selection=cache.selection(dict(url=URL,quality='90',start=3.1,end=3.2))
            self.assertTrue(selection['ready'])
            job=self.e.add_jobs(dict(url=URL,quality='90',mode='fast',preset='compatible',clips=[dict(start=3.1,end=3.2,player_segments=selection['segments'])]))['added'][0]
            payload=next(j['payload'] for j in self.e.jobs() if j['id']==job)
            output=self.root/'test.mkv'
            result=subprocess.run(self.e.scenes.segments_command(payload,output),capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            probe=json.loads(subprocess.check_output([str(ROOT/'tools/ffprobe.exe'),'-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames','-of','json',str(output)]))
            self.assertEqual(int(probe['streams'][0]['nb_read_frames']),3)

    def split(self):
        keys=[]
        for a,b in ((0,3),(3,6)):
            key=uuid.uuid4().hex;directory=self.e.scenes.root/key;directory.mkdir();media=directory/'source.mkv'
            result=subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y','-copyts','-i',str(self.media),
                '-vf',f'trim=start={a}:end={b}','-af',f'atrim=start={a}:end={b}','-c:v','ffv1','-c:a','pcm_s16le',str(media)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            import threading
            task=dict(url=URL,quality='90',duration=6,center=(a+b)/2,radius=12,mode='manual',cancel=threading.Event(),process=None)
            record=self.e.scenes.analyze(task,media,a,b)
            (directory/'analysis.json').write_text(json.dumps(record),encoding='utf-8')
            self.e.player_cache.entries[key]=dict(record=record,used=time.monotonic())
            keys.append(key)
        self.e.player_cache.session=(URL,'90',6)
        return keys

    def test_cross_window_export_no_missing_or_duplicate_frames(self):
        keys=self.split()
        result=self.e.player_cache.selection(dict(url=URL,quality='90',start=2.033333,end=4.966667))
        self.assertTrue(result['ready'],result);segments=result['segments']
        self.assertEqual([s['id'] for s in segments],keys)
        payload=dict(url=URL,quality='90',player_segments=segments)
        output=self.root/'cross.mkv'
        command=self.e.scenes.segments_command(payload,output)
        process=subprocess.run(command,capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr)
        checked=json.loads(subprocess.check_output([str(ROOT/'tools/ffprobe.exe'),'-v','error','-show_frames','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',str(output)]))
        times=[float(f['best_effort_timestamp_time']) for f in checked['frames']]
        self.assertEqual(len(times),88)
        self.assertTrue(all(.0319<=b-a<=.0341 for a,b in zip(times,times[1:])))
        self.assertAlmostEqual(times[-1]+1/30,2.933333,places=2)

    def test_missing_window_reports_gap_and_wrong_quality_rejected(self):
        keys=self.split();self.e.player_cache.entries.pop(keys[1])
        result=self.e.player_cache.selection(dict(url=URL,quality='90',start=2,end=4))
        self.assertFalse(result['ready']);self.assertEqual(result['gap'],3)
        with self.assertRaises(ValueError):self.e.player_cache.selection(dict(url=URL,quality='1080',start=2,end=4))
        with self.assertRaises(ValueError):self.e.scenes.validate_segments([dict(id=keys[0],start=True,end=30)],URL,'90')

    def test_promote_prefetch_and_cancel_old_position(self):
        cache=self.e.player_cache;cache.session=(URL,'90',200)
        task=dict(session=cache.session,begin=20,finish=44,prefetch=True,id='fake')
        cache.running=task
        with patch.object(self.e.scenes,'status',return_value=dict(message='working')),patch.object(self.e.scenes,'cancel') as cancel:
            state=cache.request(dict(url=URL,quality='90',duration=200,time=30))
            self.assertEqual(state['state'],'working');self.assertFalse(task['prefetch']);cancel.assert_not_called()
            cache.request(dict(url=URL,quality='90',duration=200,time=150))
            self.assertTrue(task['cancelled']);cancel.assert_called_once_with('fake')
            cache.pending=None;cache.running=None

    def test_eviction_protects_active_and_queued_export(self):
        keys=self.split();cache=self.e.player_cache;cache.active=keys[1]
        self.assertTrue(cache.trim(budget=3.1));self.assertNotIn(keys[0],cache.entries);self.assertIn(keys[1],cache.entries)
        self.assertFalse(cache.trim(reserve=10,budget=3.1))

    def test_player_api_requires_auth_and_valid_position(self):
        from server import LocalServer
        import threading,urllib.request,urllib.error
        server=LocalServer(self.e,ROOT/'web');threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            request=urllib.request.Request(server.origin+'/api/player/request',data=json.dumps(dict(url=URL,quality='90',duration=6,time=-1)).encode(),headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
            self.assertEqual(error.exception.code,403)
            request.add_header('X-Session-Token',server.token)
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
            self.assertEqual(error.exception.code,400)
        finally:server.shutdown();server.server_close()

    def test_restart_restores_same_source_without_download(self):
        cache=self.e.player_cache
        cache.request(dict(url=URL,quality='90',duration=6,time=0,first=True));key=self.ready()['id']
        cache.session=None;cache.entries={};cache.active=None
        with patch.object(self.e.scenes,'download_command',side_effect=AssertionError('redownload')):
            state=cache.request(dict(url=URL,quality='90',duration=6,time=3))
            self.assertEqual(state['state'],'ready');self.assertEqual(state['id'],key)

    def test_selection_and_queued_job_keep_windows_during_prune(self):
        keys=self.split();cache=self.e.player_cache
        selection=cache.selection(dict(url=URL,quality='90',start=2,end=4))
        self.assertTrue(selection['ready']);self.assertFalse(cache.trim(budget=1))
        added=self.e.add_jobs(dict(url=URL,quality='90',mode='precise',preset='compatible',clips=[dict(start=2,end=4,player_segments=selection['segments'])]))['added'][0]
        cache.keep=set();self.assertFalse(cache.trim(budget=1))
        self.assertTrue(all(key in cache.entries for key in keys));self.assertTrue(added)

    def test_silent_video_exact_export(self):
        keys=self.split()
        for key in keys:
            directory=self.e.scenes.root/key
            silent=directory/'silent.mkv'
            result=subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y','-copyts','-i',str(directory/'source.mkv'),'-an','-c:v','copy',str(silent)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            silent.replace(directory/'source.mkv')
        segments=self.e.player_cache.selection(dict(url=URL,quality='90',start=2,end=4))['segments']
        result=subprocess.run(self.e.scenes.segments_command(dict(url=URL,quality='90',player_segments=segments),self.root/'silent-export.mkv'),capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_preview_preserves_nonzero_vfr_frame_timestamps(self):
        import threading
        media=self.root/'variable.mkv';preview=self.root/'variable.mp4'
        subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y','-i',str(self.media),
            '-vf',r'select=not(eq(mod(n\,3)\,1)),setpts=PTS+20/TB','-an','-fps_mode','passthrough',
            '-c:v','libx264',str(media)],check=True,capture_output=True)
        task=dict(url=URL,quality='90',duration=26,center=23,radius=12,mode='manual',cancel=threading.Event(),process=None)
        record=self.e.scenes.analyze(task,media,20,26)
        self.e.scenes.make_preview(task,record,media,preview)
        frames=json.loads(subprocess.check_output([str(ROOT/'tools/ffprobe.exe'),'-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(preview)]))['frames']
        times=[float(f['best_effort_timestamp_time']) for f in frames]
        self.assertEqual(len(times),len(record['times']))
        self.assertTrue(all(abs(t-(original-record['times'][0]))<.0004 for t,original in zip(times,record['times'])))
