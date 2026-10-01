import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'app'))
from sources import parse_source, PlatformRedirect, platform_settings
from preview import media_url, MediaRedirect
import test_app

class SourceTests(unittest.TestCase):
    def test_links_shares_and_parts(self):
        self.assertEqual(parse_source('https://www.bilibili.com/video/BV1xx411c7mD?p=2&share_source=copy')['url'],'https://www.bilibili.com/video/BV1xx411c7mD?p=2')
        self.assertEqual(parse_source('复制链接看视频 https://v.douyin.com/Abc123/ 你好')['platform'],'douyin')
        self.assertEqual(parse_source('https://www.douyin.com/?modal_id=6961737553342991651')['id'],'6961737553342991651')
        self.assertTrue(parse_source('https://b23.tv/Abc123')['short'])
        self.assertEqual(parse_source('https://www.iesdouyin.com/share/video/6961737553342991651/')['url'],'https://www.douyin.com/video/6961737553342991651')

    def test_reject_wrong_hosts_and_nonvideo(self):
        for url in ('https://bilibili.com.evil.test/video/BV1xx411c7mD','https://www.bilibili.com/video/BV1xx411c7mD?p=0','https://www.douyin.com/user/123','https://127.0.0.1/a','http://www.douyin.com/video/6961737553342991651','https://u:p@b23.tv/abc','https://b23.tv:123/abc'):
            with self.subTest(url=url),self.assertRaises(ValueError):parse_source(url)

    def test_redirect_cannot_change_platform_or_target_local(self):
        import urllib.request
        req=urllib.request.Request('https://b23.tv/abc')
        for url in ('https://127.0.0.1/a','https://evil.test/a','https://www.douyin.com/video/6961737553342991651'):
            with self.assertRaises(ValueError):PlatformRedirect('bilibili').redirect_request(req,None,302,'',{},url)

    def test_cookie_and_proxy_isolation(self):
        settings=dict(proxy='http://yt.test:123',cookies='youtube.txt',bilibili_cookies='bili.txt',douyin_cookies='douyin.txt')
        self.assertEqual(platform_settings(settings,'bilibili')['cookies'],'bili.txt')
        self.assertEqual(platform_settings(settings,'douyin')['proxy'],'')
        self.assertEqual(platform_settings(settings,'youtube')['cookies'],'youtube.txt')

    def test_preview_rejects_untrusted_hosts_and_private_resolution(self):
        with self.assertRaises(ValueError):media_url('https://localhost/a.mp4')
        with patch('preview.socket.getaddrinfo',return_value=[(0,0,0,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):media_url('https://test.bilivideo.com/a.mp4')

    def test_short_link_resolution_keeps_part_and_rejects_unresolved(self):
        from sources import resolve_source
        from unittest.mock import Mock
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.geturl.return_value='https://www.bilibili.com/video/BV1xx411c7mD?p=3'
        opener=Mock();opener.open.return_value=response
        with patch('sources.urllib.request.build_opener',return_value=opener):
            self.assertEqual(resolve_source('https://b23.tv/abc')['part'],3)
            response.geturl.return_value='https://b23.tv/abc'
            with self.assertRaises(ValueError):resolve_source('https://b23.tv/abc')

    def test_preview_carries_range_and_referer_without_exposing_cookie_header(self):
        from preview import open_media
        from unittest.mock import Mock
        entry=dict(format=dict(url='https://test.bilivideo.com/a.mp4',http_headers={'Referer':'https://www.bilibili.com/','Host':'evil.test','Cookie':'secret'}),settings=dict(proxy='',cookies=''))
        opener=Mock()
        with patch('preview.socket.getaddrinfo',return_value=[(0,0,0,'',('8.8.8.8',443))]),patch('preview.urllib.request.build_opener',return_value=opener):
            open_media(entry,'bytes=0-100',Path(__file__).resolve().parent.parent/'tools')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Range'),'bytes=0-100')
        self.assertEqual(request.get_header('Referer'),'https://www.bilibili.com/')
        self.assertIsNone(request.get_header('Host'));self.assertIsNone(request.get_header('Cookie'))

class PlatformPipelineTests(unittest.TestCase):
    setUp=test_app.CoreTests.setUp
    tearDown=test_app.CoreTests.tearDown
    payload=test_app.CoreTests.payload
    def test_platform_jobs_snapshot_part_and_range(self):
        self.e.tools=Path(__file__).resolve().parent.parent/'tools'
        p=self.payload();p['url']='https://www.bilibili.com/video/BV1xx411c7mD?p=2'
        job=self.e.add_jobs(p)['added'][0];payload=self.e.jobs()[0]['payload']
        self.assertEqual(payload['platform'],'bilibili');self.assertEqual(payload['part'],2)
        command=self.e.command_for(job,payload,self.e.settings())
        self.assertEqual(command[-1],p['url']);self.assertIn('--download-sections',command)
        self.assertIn('',command)

    def test_metadata_translates_douyin_and_preview_handles(self):
        from types import SimpleNamespace
        self.e.tools=Path(__file__).resolve().parent.parent/'tools'
        info=dict(id='6961737553342991651',title='竖屏示例',duration=20,formats=[dict(url='https://video.douyinvod.com/a.mp4',height=1920,width=1080,ext='mp4',vcodec='avc1',acodec='aac',protocol='https')])
        with patch('core.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(info),stderr='')):
            data=self.e.metadata('https://www.douyin.com/video/6961737553342991651')
        self.assertEqual(data['platform'],'douyin');self.assertEqual(data['qualities'][0]['height'],1920)
        self.assertNotIn('url',data['preview']);self.assertIsNone(data['preview']['audio'])
        self.assertEqual(self.e.preview_source(data['preview']['video'])['format']['url'],info['formats'][0]['url'])

    def test_dash_preview_selects_separate_audio(self):
        preview=self.e.register_preview(dict(formats=[dict(url='https://cdn.bilivideo.com/v',ext='mp4',height=720,vcodec='avc1',acodec='none'),dict(url='https://cdn.bilivideo.com/a',ext='m4a',vcodec='none',acodec='mp4a')]),self.e.settings())
        self.assertTrue(preview['audio'])
        self.e.preview_sources[preview['video']]['created']=time.time()-7201
        with self.assertRaises(ValueError):self.e.preview_source(preview['video'])

    def test_douyin_watermarked_download_is_excluded_from_preview_and_cache(self):
        from types import SimpleNamespace
        self.e.tools=test_app.ROOT/'tools'
        formats=[dict(format_id='download_addr',format_note='Download video, watermarked',url='https://video.douyinvod.com/w',height=1920,ext='mp4',vcodec='h264',acodec='aac'),
            dict(format_id='play_addr',format_note='Direct video',url='https://video.douyinvod.com/p',height=720,ext='mp4',vcodec='h264',acodec='aac')]
        info=dict(title='test',duration=19,formats=formats)
        with patch('core.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(info),stderr='')):
            data=self.e.metadata('https://www.douyin.com/video/6961737553342991651')
        self.assertEqual(data['qualities'][0]['height'],720)
        self.assertEqual(self.e.preview_source(data['preview']['video'])['format']['format_id'],'play_addr')
        cached=next(iter(self.e.metadata_cache.values()))[2]
        self.assertEqual(len(cached['formats']),1)

class PreviewHTTPTests(unittest.TestCase):
    setUp=test_app.HTTPTests.setUp
    tearDown=test_app.HTTPTests.tearDown
    request=test_app.HTTPTests.request

    def test_session_media_handle_and_range_forwarding(self):
        from unittest.mock import Mock
        entry=self.e.register_preview(dict(formats=[dict(url='https://test.douyinvod.com/a.mp4',ext='mp4',height=720,vcodec='avc1',acodec='aac')]),self.e.settings())
        def upstream(entry,header,tools):
            start,end=map(int,header[6:].split('-'));raw=b'0123456789'[start:end+1]
            response=io.BytesIO(raw);response.status=206;response.headers={'Content-Length':str(len(raw)),'Content-Range':f'bytes {start}-{end}/10','Accept-Ranges':'bytes'}
            return response
        with patch('preview.open_media',side_effect=upstream) as opened:
            import urllib.request
            path='/s/'+self.server.token+'/preview/'+entry['video']
            with urllib.request.urlopen(urllib.request.Request(self.server.origin+path,headers={'Range':'bytes=0-3'})) as response:
                self.assertEqual(response.status,206);self.assertEqual(response.read(),b'0123')
            self.assertEqual(opened.call_args.args[1],'bytes=0-9')
            count=opened.call_count
            with urllib.request.urlopen(urllib.request.Request(self.server.origin+path,headers={'Range':'bytes=2-5'})) as response:
                self.assertEqual(response.read(),b'2345')
            self.assertEqual(opened.call_count,count)
        with patch('preview.open_media') as opened:
            import urllib.error
            with self.assertRaises(urllib.error.HTTPError):self.request('/s/wrong/preview/'+entry['video'])
            opened.assert_not_called()
