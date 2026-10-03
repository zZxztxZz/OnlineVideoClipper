import json
import time
import unittest
from unittest.mock import patch
import test_app
from audio_tracks import audio_tracks,choose_audio,audio_selector,track_id

URL='https://www.youtube.com/watch?v=S9STizATKjE'

def fixture():
    video=dict(format_id='299',url='https://cdn.googlevideo.com/video.mp4',ext='mp4',protocol='https',height=1080,vcodec='avc1',acodec='none')
    def audio(language,rate,preference):
        return dict(format_id='140-'+language,url='https://cdn.googlevideo.com/'+language+'.m4a',ext='m4a',protocol='https',language=language,language_preference=preference,abr=rate,vcodec='none',acodec='mp4a.40.2')
    return dict(formats=[video,audio('de-DE',130,-1),audio('en-US',129,10),audio('ja',130,-1),dict(audio('ja',48,-1),format_id='139-ja')])

class AudioTrackTests(unittest.TestCase):
    setUp=test_app.CoreTests.setUp
    tearDown=test_app.CoreTests.tearDown

    def test_groups_bitrates_and_prioritizes_original_language(self):
        info=fixture();tracks=audio_tracks(info)
        self.assertEqual(len(tracks),3)
        self.assertEqual(tracks[0]['id'],'en-US');self.assertTrue(tracks[0]['original'])
        self.assertEqual(choose_audio(info['formats'])['language'],'en-US')
        self.assertEqual(choose_audio(info['formats'],'ja')['abr'],130)
        with self.assertRaises(ValueError):choose_audio(info['formats'],'zh-CN')

    def test_preview_switch_reuses_video_and_selects_requested_audio(self):
        info=fixture();settings=self.e.settings()
        english=self.e.register_preview(info,settings,'1080')
        japanese=self.e.register_preview(info,settings,'1080','ja')
        self.assertEqual(english['video'],japanese['video'])
        self.assertNotEqual(english['audio'],japanese['audio'])
        self.assertEqual(self.e.preview_source(english['audio'])['format']['language'],'en-US')
        self.assertEqual(self.e.preview_source(japanese['audio'])['format']['language'],'ja')
        self.e.preview_origin='http://127.0.0.1:1234/s/test/'
        cached=self.e.cached_preview_info(info,'1080','ja')
        self.assertEqual(cached['formats'][1]['language'],'ja')
        self.assertIn(japanese['audio'],cached['formats'][1]['url'])

    def test_description_track_is_distinct_from_original_same_language(self):
        info=fixture();description=dict(info['formats'][2],format_id='description',format_note='English audio description',abr=160)
        info['formats'].append(description)
        tracks=audio_tracks(info)
        self.assertTrue(any(t['id']=='en-US:description' and t['description'] for t in tracks))
        self.assertEqual(choose_audio(info['formats'],'en-US')['format_id'],'140-en-US')
        self.assertEqual(choose_audio(info['formats'],'en-US:description')['format_id'],'description')
        self.assertEqual(choose_audio(info['formats'])['format_id'],'140-en-US')
        self.assertIn('format_note~=',audio_selector('en-US:description'))

    def test_opus_track_previews_when_aac_missing_and_original_still_wins(self):
        info=fixture()
        opus=dict(info['formats'][2],format_id='opus-original',ext='webm',acodec='opus',abr=160)
        info['formats'].append(opus)
        self.assertEqual(choose_audio(info['formats'],'en-US')['ext'],'m4a')
        info['formats']=[f for f in info['formats'] if f.get('format_id')!='140-en-US']
        handle=self.e.register_preview(info,self.e.settings(),'1080','en-US')
        self.assertEqual(self.e.preview_source(handle['audio'])['format']['acodec'],'opus')

    def test_downloads_keep_language_and_distinct_job_identity(self):
        self.e.tools=test_app.ROOT/'tools'
        payload=test_app.CoreTests.payload(self);payload['url']=URL;payload['audio_track']='en-US'
        self.assertEqual(len(self.e.add_jobs(payload)['added']),1)
        payload['audio_track']='ja'
        self.assertEqual(len(self.e.add_jobs(payload)['added']),1)
        job=next(j['payload'] for j in self.e.jobs() if j['payload']['audio_track']=='ja')
        command=self.e.command_for('test',job,self.e.settings())
        selector=command[command.index('-f')+1]
        self.assertIn('[language=ja]',selector);self.assertNotIn('/best',selector)
        job['quality']='audio'
        command=self.e.command_for('test',job,self.e.settings())
        self.assertEqual(command[command.index('-f')+1],audio_selector('ja',m4a=True))

    def test_audio_id_validation_and_source_cache_mismatch(self):
        for value in ('en-US]/best',True,{},'a'*65):
            with self.assertRaises(ValueError):track_id(value)
        record=dict(url=URL,quality='1080',audio_track='en-US',times=[0,.1],last_end=.2)
        with patch.object(self.e.scenes,'record',return_value=record):
            self.e.scenes.validate_segments([dict(id='a'*32,start=0,end=2)],URL,'1080','en-US')
            with self.assertRaises(ValueError):self.e.scenes.validate_segments([dict(id='a'*32,start=0,end=2)],URL,'1080','ja')

    def test_frame_download_uses_selected_cached_audio_and_cache_session_isolated(self):
        info=fixture();settings=self.e.settings();self.e.tools=test_app.ROOT/'tools'
        self.e.metadata_cache[self.e.cache_key(URL,settings)]=(time.time(),{},info)
        self.e.preview_origin='http://127.0.0.1:1234/s/test/'
        self.e.register_preview(info,settings,'1080','ja')
        directory=self.e.data/'sample';directory.mkdir()
        command=self.e.scenes.download_command(dict(url=URL,quality='1080',audio_track='ja'),0,1,directory)
        self.assertIn('[language=ja]',command[command.index('-f')+1])
        cached=json.loads((directory/'source-info.json').read_text(encoding='utf-8'))
        self.assertEqual(cached['formats'][1]['language'],'ja')
        cache=self.e.player_cache
        cache.session=(URL,'1080',97,'en-US')
        cache.entries={'a'*32:dict(record=dict(times=[0,1],last_end=2),used=0)}
        with cache.lock,patch.object(self.e.scenes,'prune'):
            cache.request(dict(url=URL,quality='1080',audio_track='ja',duration=97,time=0))
            self.assertFalse(cache.entries)
            self.assertEqual(cache.pending['audio_track'],'ja')
            cache.pending=None
