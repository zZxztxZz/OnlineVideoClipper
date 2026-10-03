"""A real two-language fixture verifies the audio in downloaded output."""
import json
from pathlib import Path
import math
import struct
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import test_app
from test_audio_tracks import URL

ROOT=test_app.ROOT

class AudioExportTests(unittest.TestCase):
    setUp=test_app.CoreTests.setUp
    tearDown=test_app.CoreTests.tearDown

    def test_downloaded_japanese_track_has_selected_tone(self):
        self.e.tools=ROOT/'tools';directory=self.e.data/'media';directory.mkdir()
        video=directory/'video.mp4'
        subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y','-f','lavfi','-i',
            'color=red:s=160x90:r=30:d=2','-an','-c:v','libx264',str(video)],check=True)
        for language,tone in (('en-US',440),('ja',880)):
            subprocess.run([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-y','-f','lavfi','-i',
                f'sine=frequency={tone}:sample_rate=48000:duration=2','-c:a','aac',str(directory/(language+'.m4a'))],check=True)
        class MediaHandler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                raw=(directory/self.path.lstrip('/')).read_bytes();start=0;end=len(raw)-1
                if self.headers.get('Range'):
                    begin,finish=self.headers['Range'][6:].split('-');start=int(begin or 0);end=min(end,int(finish)) if finish else end
                    self.send_response(206);self.send_header('Content-Range',f'bytes {start}-{end}/{len(raw)}')
                else:self.send_response(200)
                self.send_header('Content-Length',str(end-start+1));self.end_headers()
                try:self.wfile.write(raw[start:end+1])
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),MediaHandler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        origin=f'http://127.0.0.1:{server.server_port}/'
        info=dict(id='audiofixture',title='Audio fixture',duration=2,extractor='generic',extractor_key='Generic',webpage_url=URL,
            formats=[dict(format_id='video',url=origin+'video.mp4',protocol='http',ext='mp4',height=90,width=160,vcodec='avc1',acodec='none'),
                *[dict(format_id=language,url=origin+language+'.m4a',protocol='http',ext='m4a',vcodec='none',acodec='mp4a.40.2',language=language,abr=128) for language in ('en-US','ja')]])
        self.e.metadata_cache[self.e.cache_key(URL,self.e.settings())]=(time.time(),{},info)
        payload=test_app.CoreTests.payload(self);payload.update(url=URL,quality='90',audio_track='ja',full=True,duration=2)
        key=self.e.add_jobs(payload)['added'][0]
        row=next(j for j in self.e.jobs() if j['id']==key)
        original=self.e.command_for
        def command(*args):
            cmd=original(*args)
            for i in range(len(cmd)-2,0,-1):
                if cmd[i].startswith('ffmpeg_i:-tls_verify'):del cmd[i-1:i+1]
            return cmd
        try:
            with patch.object(self.e,'command_for',side_effect=command):self.e.execute(key,row['payload'],1)
            row=next(j for j in self.e.jobs() if j['id']==key)
            self.assertEqual(row['state'],'complete',row['detail'])
            raw=subprocess.check_output([str(ROOT/'tools/ffmpeg.exe'),'-v','error','-i',row['output'],
                '-ss','0.2','-t','1','-vn','-ac','1','-ar','8000','-f','s16le','pipe:1'])
            samples=struct.unpack('<'+'h'*(len(raw)//2),raw)
            energy=lambda hz:abs(sum(value*complex(math.cos(2*math.pi*hz*i/8000),math.sin(2*math.pi*hz*i/8000)) for i,value in enumerate(samples)))
            self.assertGreater(energy(880),energy(440)*10)
            self.assertFalse(Path(row['output']+'.source.json').exists())
        finally:server.shutdown();server.server_close()
