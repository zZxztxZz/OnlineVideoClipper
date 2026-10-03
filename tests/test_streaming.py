import io
import json
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import test_app


class StreamingTests(unittest.TestCase):
    setUp=test_app.HTTPTests.setUp
    tearDown=test_app.HTTPTests.tearDown

    def entry(self):
        handle=self.e.register_preview(dict(formats=[dict(url='https://cdn.bilivideo.com/stream.mp4',ext='mp4',height=720,vcodec='avc1',acodec='aac')]),self.e.settings())
        return handle,self.e.preview_source(handle['video'])

    def upstream(self,raw):
        def open_source(entry,header,tools):
            start,end=map(int,header[6:].split('-'))
            data=io.BytesIO(raw[start:end+1]);data.status=206;data.headers={'Content-Range':f'bytes {start}-{end}/{len(raw)}'}
            return data
        return open_source

    def test_first_bytes_arrive_before_entire_cache_block_is_read(self):
        _,entry=self.entry();raw=b'a'*(256*1024)
        with patch('preview.open_media',side_effect=self.upstream(raw)):
            chunks=self.e.byte_cache.iter_block(entry,0)
            first=next(chunks)
            self.assertEqual(len(first),64*1024)
            self.assertFalse(list(self.e.byte_cache.root.glob('*.bin')))
            self.assertEqual(first+b''.join(chunks),raw)
            self.assertEqual(len(list(self.e.byte_cache.root.glob('*.bin'))),1)
        with patch('preview.open_media',side_effect=AssertionError('network re-read')):
            self.assertEqual(self.e.byte_cache.block(entry,0),raw)

    def test_cancelled_stream_discards_partial_file(self):
        _,entry=self.entry()
        with patch('preview.open_media',side_effect=self.upstream(b'a'*(256*1024))):
            chunks=self.e.byte_cache.iter_block(entry,0);next(chunks);chunks.close()
        self.assertFalse(list(self.e.byte_cache.root.glob('*.tmp')))
        self.assertFalse(list(self.e.byte_cache.root.glob('*.bin')))

    def test_exact_extractor_size_skips_probe_but_validates_range(self):
        _,entry=self.entry();raw=b'frame bytes';entry['format']['filesize']=len(raw)
        with patch('preview.open_media',side_effect=self.upstream(raw)) as opened:
            self.assertEqual(self.e.byte_cache.block(entry,0),raw)
            self.assertEqual(opened.call_count,1)
            self.assertEqual(opened.call_args.args[1],f'bytes=0-{len(raw)-1}')
        _,entry=self.entry();entry['format']=dict(entry['format'],url='https://cdn.bilivideo.com/other.mp4',filesize=50)
        with patch('preview.open_media',side_effect=self.upstream(raw)),self.assertRaises(ValueError):
            self.e.byte_cache.block(entry,0)

    def test_bad_range_response_is_rejected(self):
        _,entry=self.entry();data=io.BytesIO(b'wrong');data.status=200;data.headers={}
        with patch('preview.open_media',return_value=data),self.assertRaises(ValueError):self.e.byte_cache.describe(entry)

    def test_suffix_seek_and_invalid_range(self):
        handle,entry=self.entry();url=self.server.url+'preview/'+handle['video']
        with patch('preview.open_media',side_effect=self.upstream(b'0123456789')):
            with urllib.request.urlopen(urllib.request.Request(url,headers={'Range':'bytes=-3'})) as response:
                self.assertEqual(response.read(),b'789');self.assertEqual(response.headers['Content-Range'],'bytes 7-9/10')
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(url,headers={'Range':'bytes=100-'}))
            self.assertEqual(error.exception.code,416)

    def test_frame_reader_uses_cached_local_stream_without_browser_throttle(self):
        handle,entry=self.entry();self.e.preview_origin=self.server.url
        info=self.e.cached_preview_info(dict(formats=[entry['format']]))
        self.assertIn('?reader=1',info['formats'][0]['url'])
        self.assertIn('FrameReader',info['formats'][0]['http_headers']['User-Agent'])
        entry['flow']=False;entry['flow_time']=time.monotonic();self.e.byte_cache.BLOCK=4
        with patch('preview.open_media',side_effect=self.upstream(b'0123456789')):
            request=urllib.request.Request(info['formats'][0]['url'],headers={'Range':'bytes=0-9','User-Agent':'Chrome/test'})
            with urllib.request.urlopen(request,timeout=2) as response:self.assertEqual(response.read(),b'0123456789')

    def test_manual_frame_request_skips_detection_and_proxy_encoding(self):
        import test_scenes
        test_scenes.SceneTests.setUpClass();fixture=test_scenes.SceneTests();fixture.setUp()
        try:
            with patch.object(fixture.e.scenes,'detect_cuts',side_effect=AssertionError('scanning')),patch.object(fixture.e.scenes,'make_preview',side_effect=AssertionError('encoding')):
                fixture.e.player_cache.request(dict(url=test_scenes.URL,quality='90',duration=6,time=2,frames_only=True))
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    state=fixture.e.player_cache.status()
                    if state['state']!='working':break
                    time.sleep(.03)
                self.assertEqual(state['state'],'ready',state)
                self.assertEqual(state['record']['preview_version'],2)
                self.assertFalse((fixture.e.scenes.root/state['id']/'preview.mp4').exists())
        finally:fixture.tearDown();test_scenes.SceneTests.tearDownClass()

    def test_flow_and_stream_endpoints_require_auth(self):
        for endpoint in ('stream','flow'):
            request=urllib.request.Request(self.server.origin+'/api/player/'+endpoint,data=b'{}',headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
            self.assertEqual(error.exception.code,403)

    def test_cached_http_frame_reader_does_not_receive_https_input_options(self):
        import test_scenes
        from server import LocalServer
        import threading
        test_scenes.SceneTests.setUpClass();fixture=test_scenes.SceneTests();fixture.setUp();fixture.download_patch.stop()
        server=LocalServer(fixture.e,test_app.ROOT/'web');threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            source=fixture.e.stream_preview(test_scenes.URL,'90')
            fixture.e.preview_source(source['video'])['flow']=False
            fixture.e.preview_source(source['video'])['flow_time']=time.monotonic()
            def open_fixture(entry,header,tools):
                return urllib.request.urlopen(urllib.request.Request(entry['format']['url'],headers={'Range':header}))
            with patch('preview.open_media',side_effect=open_fixture):
                fixture.e.player_cache.request(dict(url=test_scenes.URL,quality='90',duration=6,time=2,frames_only=True))
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    state=fixture.e.player_cache.status()
                    if state['state']!='working':break
                    time.sleep(.03)
                self.assertEqual(state['state'],'ready',state)
                self.assertTrue(list(fixture.e.byte_cache.root.glob('*.bin')))
        finally:server.shutdown();server.server_close();fixture.tearDown();test_scenes.SceneTests.tearDownClass()
