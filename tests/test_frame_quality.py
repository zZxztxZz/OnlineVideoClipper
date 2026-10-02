"""Original frame dimensions, legacy-cache isolation and copy API validation."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import Mock,patch
import test_app
from core import Engine
from server import LocalServer
from PIL import Image

ROOT=test_app.ROOT

class FrameQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'work')
        self.engine=Engine(Path(self.temp.name),workers=False)
        self.engine.tools=ROOT/'tools'
        self.key='a'*32
        self.directory=self.engine.scenes.root/self.key
        self.directory.mkdir()
        self.record=dict(times=[0,.1],last_end=.2,width=1920,height=1080,quality='1080')
        (self.directory/'analysis.json').write_text(json.dumps(self.record),encoding='utf-8')
        subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y','-f','lavfi','-i',
            'testsrc2=s=1920x1080:r=10:d=0.2','-c:v','ffv1',str(self.directory/'source.mkv')],check=True)

    def tearDown(self):
        self.engine.stop();self.temp.cleanup()

    def test_original_size_lossless_frame_ignores_legacy_thumbnail_and_reuses_cache(self):
        Image.new('RGB',(720,404)).save(self.directory/'frame-1.jpg')
        result=self.engine.scenes.frame(self.key,1)
        path=self.engine.scenes.asset(self.key,result['image'].split('/')[-1])
        with Image.open(path) as image:
            self.assertEqual(image.size,(1920,1080))
            self.assertEqual(image.format,'PNG')
        self.assertEqual(result['time'],.1)
        with patch.object(self.engine.scenes,'run',side_effect=AssertionError('cached frame must be reused')):
            self.assertEqual(self.engine.scenes.full_frame(self.key,1),path)
        for index in (-1,2,True,'1'):
            with self.assertRaises(ValueError):self.engine.scenes.full_frame(self.key,index)

    def test_clipboard_api_requires_session_desktop_and_valid_frame(self):
        server=LocalServer(self.engine,ROOT/'web')
        threading.Thread(target=server.serve_forever,daemon=True).start()
        def request(index=1,token=True):
            req=urllib.request.Request(server.origin+'/api/scene/copy-frame',
                data=json.dumps(dict(id=self.key,index=index)).encode(),
                headers={'X-Session-Token':server.token} if token else {})
            return json.loads(urllib.request.urlopen(req).read())
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:request(token=False)
            self.assertEqual(error.exception.code,403)
            with self.assertRaises(urllib.error.HTTPError) as error:request()
            self.assertEqual(error.exception.code,400)
            server.desktop=Mock()
            server.desktop.copy_frame.return_value=dict(ok=True,width=1920,height=1080)
            self.assertEqual(request()['width'],1920)
            server.desktop.copy_frame.assert_called_once_with(self.directory/'fullframe-1.png')
            with self.assertRaises(urllib.error.HTTPError):request(index=99)
            self.assertEqual(server.desktop.copy_frame.call_count,1)
        finally:server.shutdown();server.server_close()
