import json
from contextlib import closing
import sqlite3
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import test_app
from core import Engine, failure_kind
from parts import fetch_parts
from sources import parse_source


class ControlTests(unittest.TestCase):
    setUp=test_app.CoreTests.setUp
    tearDown=test_app.CoreTests.tearDown
    payload=test_app.CoreTests.payload

    def test_preferences_remember_last_directory_without_changing_default(self):
        default=self.e.settings()['output_dir']
        directory=self.root/'chosen'
        self.e.add_jobs(self.payload(output_dir=str(directory),quality='720',mode='precise',preset='original'))
        self.e.save_settings(dict(volume=27,muted=True,window_width=1000,window_height=740))
        restarted=Engine(self.root,workers=False)
        s=restarted.settings()
        self.assertEqual(s['output_dir'],default)
        self.assertEqual(s['last_output_dir'],str(directory.resolve()))
        self.assertEqual((s['quality'],s['mode'],s['preset']),('720','precise','original'))
        self.assertEqual((s['volume'],s['muted'],s['window_width']),(27,True,1000))
        restarted.stop()

    def test_paused_task_stays_paused_after_restart_and_can_resume(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        self.e.patch(job,state='downloading',attempt=1)
        process=Mock()
        self.e.processes[job]=process
        with patch.object(self.e,'kill') as kill:
            self.e.action(job,'pause')
            kill.assert_called_once_with(process)
        self.assertEqual(self.e.jobs()[0]['state'],'paused')
        with self.assertRaises(ValueError): self.e.action(job,'resume')
        self.e.processes.clear()
        restarted=Engine(self.root,workers=False)
        self.assertTrue(restarted.interrupted(job))
        self.assertEqual(restarted.jobs()[0]['attempt'],0)
        restarted.action(job,'resume')
        self.assertEqual(restarted.jobs()[0]['state'],'queued')
        self.assertFalse(restarted.interrupted(job))
        restarted.stop()

    def test_priority_and_movement_persist(self):
        ids=[self.e.add_jobs(dict(self.payload(),clips=[dict(start=i,end=i+1,name=str(i))]))['added'][0] for i in range(3)]
        self.e.action(ids[2],'first')
        self.assertEqual([j['id'] for j in self.e.jobs()],[ids[2],ids[0],ids[1]])
        self.e.action(ids[2],'down')
        self.assertEqual([j['id'] for j in self.e.jobs()],[ids[0],ids[2],ids[1]])
        self.e.action(ids[1],'up')
        self.assertEqual([j['id'] for j in self.e.jobs()],[ids[0],ids[1],ids[2]])

    def test_queue_pause_prevents_new_workers_and_resumes(self):
        self.e.save_settings(dict(queue_paused=True,concurrency=1))
        job=self.e.add_jobs(self.payload())['added'][0]
        calls=threading.Event()
        with patch.object(Engine,'execute',side_effect=lambda *args:calls.set()):
            worker=Engine(self.root,workers=True)
            try:
                self.assertFalse(calls.wait(.35))
                worker.save_settings(dict(queue_paused=False))
                self.assertTrue(calls.wait(2))
            finally: worker.stop()

    def test_metrics_and_failure_categories_are_structured(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        self.e.progress_line(job,'PROGRESS:'+json.dumps(dict(downloaded_bytes=50,total_bytes=100,speed=25,eta=2,info_dict='secret')))
        row=self.e.jobs()[0]
        self.assertEqual(row['progress'],50)
        self.assertEqual(row['metrics']['speed'],25)
        self.assertNotIn('info_dict',row['metrics'])
        for text,kind in [('Fresh cookies required','auth'),('Requested format is not available','format'),('No space left','storage'),('Connection timed out','network')]:
            self.assertEqual(failure_kind(text),kind)

    def test_failed_task_can_change_output_and_retry(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        self.e.patch(job,state='failed',failure_kind='storage')
        target=self.root/'new'/'rename.mp4'
        self.e.action(job,'relocate',str(target))
        self.e.action(job,'retry')
        row=self.e.jobs()[0]
        self.assertEqual(row['payload']['filename'],'rename.mp4')
        self.assertEqual(row['payload']['output_dir'],str(target.parent))
        self.assertEqual(row['failure_kind'],'')

    def test_pause_prevents_subprocess_spawn(self):
        job=self.e.add_jobs(self.payload())['added'][0]
        self.e.action(job,'pause')
        with patch.object(self.e,'command_for',return_value=['fake']),patch('core.subprocess.Popen') as spawn:
            self.e.execute(job,self.e.jobs()[0]['payload'],1)
            spawn.assert_not_called()

    def test_part_lists_only_return_multiple_named_parts(self):
        source=parse_source('https://www.bilibili.com/video/BV1bK411W797')
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        opener=Mock()
        opener.open.return_value=response
        with patch('parts.urllib.request.build_opener',return_value=opener):
            response.read.return_value=json.dumps(dict(code=0,data=[dict(page=1,part='第一讲'),dict(page=2,part='第二讲')])).encode()
            pages,error=fetch_parts(source,{})
            self.assertEqual([p['title'] for p in pages],['第一讲','第二讲'])
            response.read.return_value=json.dumps(dict(code=0,data=[dict(page=1,part='只有一集')])).encode()
            self.assertEqual(fetch_parts(source,{}),([],''))
            opener.open.side_effect=OSError('timeout')
            self.assertTrue(fetch_parts(source,{})[1])

    def test_legacy_database_migration_preserves_existing_history(self):
        root=self.root/'legacy'
        data=root/'data'
        data.mkdir(parents=True)
        with closing(sqlite3.connect(data/'queue.sqlite3')) as c, c:
            c.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT NOT NULL,state TEXT NOT NULL,progress REAL DEFAULT 0,attempt INTEGER DEFAULT 0,due REAL DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL,message TEXT DEFAULT '',detail TEXT DEFAULT '',output TEXT DEFAULT '')")
            c.execute("INSERT INTO jobs(id,payload,state,created,updated) VALUES(?,?,?,?,?)",('history',json.dumps(dict(title='old')), 'complete',1,1))
        migrated=Engine(root,workers=False)
        self.assertEqual(migrated.jobs()[0]['id'],'history')
        self.assertEqual(migrated.jobs()[0]['state'],'complete')
        self.assertEqual(migrated.jobs()[0]['metrics'],{})
        migrated.stop()
