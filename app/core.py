"""Persistent download queue; all application data lives beside the launcher."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import uuid
from sources import parse_source, resolve_source, platform_settings, NAMES
from parts import fetch_parts

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
ACTIVE = ('downloading', 'processing')

def video_url(value):
    source=parse_source(value)
    if source['short']:
        raise ValueError('请先解析分享短链接，再加入下载队列')
    return source['url'], source['id']

def seconds(value):
    try:
        if isinstance(value, str) and ':' in value:
            parts = value.split(':')
            if len(parts) not in (2, 3):
                raise ValueError()
            nums = [float(x) for x in parts]
            if any(x < 0 for x in nums) or any(x >= 60 for x in nums[1:]):
                raise ValueError()
            n = sum(x * 60 ** i for i, x in enumerate(reversed(nums)))
        else:
            n = float(value)
        if not math.isfinite(n) or n < 0:
            raise ValueError()
        return round(n, 3)
    except (ValueError, TypeError):
        raise ValueError('时间格式无效，请使用 00:01:23.500') from None

def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f%]', '_', str(value))
    return value.strip(' .')[:90] or 'clip'

def output_target(value,quality,preset):
    target=Path(str(value))
    extension='.m4a' if quality=='audio' else '.mp4' if preset=='compatible' else '.mkv'
    if not target.is_absolute() or target.suffix.lower()!=extension:
        raise ValueError('请选择绝对保存路径，文件扩展名应为 '+extension)
    if len(target.name)>180 or re.search(r'[<>:"/\\|?*\x00-\x1f]',target.name) or target.name!=target.name.strip(' .') or target.stem.upper().split('.')[0].rstrip(' ') in {
        'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        raise ValueError('保存文件名含有 Windows 不支持的字符或名称')
    target.parent.mkdir(parents=True,exist_ok=True)
    return target.parent.resolve()/target.name

def classify_error(raw):
    s = raw.lower()
    if any(x in s for x in ('sign in', 'login required', 'cookie', 'confirm your age', 'private video', 'members-only', 'fresh cookies', 'verification', 'captcha','401 unauthorized','重新连接','登录','平台验证')):
        return False, '此视频需要登录、新 Cookie 或平台验证。请连接对应平台后重新解析。'
    if any(x in s for x in ('video unavailable', 'video has been removed', 'copyright', 'not available in your country', 'unsupported url')):
        return False, '视频不可用、已删除，或当前地区无法访问。'
    if 'requested format is not available' in s:
        return False, '所选格式目前不可用，请重新解析视频并选择其他清晰度。'
    if any(x in s for x in ('no space left', 'disk full', 'permission denied', 'access is denied','磁盘空间','拒绝访问')):
        return False, '无法写入文件，请检查剩余空间和保存目录权限。'
    return True, '网络或媒体处理失败；将按重试策略重新解析并尝试。'

def failure_kind(raw):
    retry,message=classify_error(raw)
    if retry:
        return 'network' if any(x in raw.lower() for x in ('http','timed out','connection','network','dns','resolve','certificate')) else 'processing'
    if 'Cookie' in message: return 'auth'
    if '清晰度' in message: return 'format'
    if '空间' in message: return 'storage'
    return 'unavailable'

class Engine:
    def __init__(self, root, workers=True):
        self.root = Path(root)
        self.data = self.root / 'data'
        self.data.mkdir(parents=True, exist_ok=True)
        self.tools = self.root / 'tools'
        self.db = self.data / 'queue.sqlite3'
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.processes = {}
        self.cancelled = set()
        self.paused = set()
        self.executing = set()
        self.threads = []
        self.metadata_cache = {}
        self.preview_sources = {}
        self.native = None
        self.wake = threading.Event()
        self.metadata_slots = threading.BoundedSemaphore(2)
        self.updating = False
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT NOT NULL,
              state TEXT NOT NULL, progress REAL DEFAULT 0, attempt INTEGER DEFAULT 0,
              due REAL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
              message TEXT DEFAULT '', detail TEXT DEFAULT '', output TEXT DEFAULT '');
            CREATE INDEX IF NOT EXISTS queue_order ON jobs(state,due,created);
            ''')
            columns={r['name'] for r in c.execute('PRAGMA table_info(jobs)')}
            for name,definition in [('queue_rank','REAL DEFAULT 0'),('metrics',"TEXT DEFAULT '{}'"),('failure_kind',"TEXT DEFAULT ''")]:
                if name not in columns: c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
            c.execute('UPDATE jobs SET queue_rank=created WHERE queue_rank=0')
            c.execute("UPDATE jobs SET state='queued',message='程序重新启动，任务等待恢复',updated=? WHERE state IN ('downloading','processing')", (time.time(),))
            self.paused={r['id'] for r in c.execute("SELECT id FROM jobs WHERE state='paused'")}
            if not c.execute("SELECT 1 FROM settings WHERE key='last_output_dir'").fetchone():
                previous=c.execute('SELECT payload FROM jobs ORDER BY created DESC LIMIT 1').fetchone()
                directory=json.loads(previous['payload']).get('output_dir','') if previous else ''
                if directory and Path(directory).is_absolute():
                    c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('last_output_dir',json.dumps(directory)))
        from scenes import SceneManager
        self.scenes=SceneManager(self)
        if workers:
            for n in range(4):
                t = threading.Thread(target=self.worker, args=(n,), daemon=True)
                t.start()
                self.threads.append(t)

    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def settings(self):
        defaults = dict(output_dir=str(self.root / 'downloads'), concurrency=2, retries=3,
                        proxy='', cookies='', bilibili_cookies='', douyin_cookies='', bilibili_proxy='', douyin_proxy='', quality='1080', mode='fast', preset='compatible',
                        last_output_dir='', volume=100, muted=False, queue_paused=False,
                        window_width=1280,window_height=900,window_maximized=False)
        with self.connect() as c:
            for row in c.execute('SELECT * FROM settings'):
                defaults[row['key']] = json.loads(row['value'])
        return defaults

    def save_settings(self, data):
        with self.lock:
            return self._save_settings(data)

    def _save_settings(self, data):
        s = self.settings()
        previous_output=s['output_dir']
        for key in s:
            if key in data:
                s[key] = data[key]
        for key, lo, hi in [('concurrency', 1, 4), ('retries', 0, 8)]:
            s[key] = int(s[key])
            if not lo <= s[key] <= hi:
                raise ValueError('并发数或重试次数超出范围')
        out = Path(str(s['output_dir']))
        if not out.is_absolute():
            raise ValueError('保存目录必须是绝对路径')
        out.mkdir(parents=True, exist_ok=True)
        s['output_dir'] = str(out.resolve())
        if 'output_dir' in data and s['output_dir']!=previous_output:
            s['last_output_dir']=s['output_dir']
        if s['last_output_dir'] and not Path(s['last_output_dir']).is_absolute():
            raise ValueError('最近保存位置必须是绝对路径')
        for key,lo,hi in [('volume',0,100),('window_width',860,7680),('window_height',620,4320)]:
            s[key]=int(s[key])
            if not lo<=s[key]<=hi: raise ValueError('音量或窗口尺寸无效')
        for key in ('muted','queue_paused','window_maximized'): s[key]=bool(s[key])
        for proxy_key in ('proxy','bilibili_proxy','douyin_proxy'):
          if s[proxy_key]:
            p = urllib.parse.urlparse(s[proxy_key])
            if p.scheme not in ('http', 'https', 'socks5', 'socks5h') or not p.hostname:
                raise ValueError('代理格式示例：http://127.0.0.1:7890')
        for cookie_key in ('cookies','bilibili_cookies','douyin_cookies'):
            if cookie_key in data and s[cookie_key] and not Path(s[cookie_key]).is_file():
                raise ValueError('Cookies 文件不存在')
        if s['mode'] not in ('fast', 'precise') or s['preset'] not in ('compatible', 'original'):
            raise ValueError('输出选项无效')
        if str(s['quality']) not in ('best','audio') and not (str(s['quality']).isdigit() and 1<=int(s['quality'])<=4320):
            raise ValueError('清晰度选项无效')
        with self.connect() as c:
            c.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [(k, json.dumps(v)) for k, v in s.items()])
        return s

    def base_command(self, settings=None):
        s = settings or self.settings()
        exe = self.tools / 'yt-dlp' / 'yt-dlp.exe'
        if not exe.is_file():
            raise ValueError('缺少下载引擎，请运行工程目录中的 setup.ps1')
        cmd = [str(exe), '--ignore-config', '--no-playlist', '--no-colors', '--encoding', 'utf-8',
               '--socket-timeout', '20', '--ffmpeg-location', str(self.tools),
               '--js-runtimes', f'deno:{self.tools / "deno.exe"}']
        if s['proxy']:
            cmd += ['--proxy', s['proxy']]
        elif s.get('_direct'):
            cmd += ['--proxy', '']
        if s['cookies']:
            cmd += ['--cookies', s['cookies']]
        return cmd

    def metadata(self, url, force=False):
        source=parse_source(url)
        s=platform_settings(self.settings(),source['platform'])
        source=resolve_source(url,s['proxy'])
        url,vid=source['url'],source['id']
        cache_key = self.cache_key(url,s)
        with self.lock:
            hit = self.metadata_cache.get(cache_key)
            if not force and hit and time.time() - hit[0] < 300:
                return dict(hit[1],cache_hit=True,elapsed_ms=0)
        with self.lock:
            if self.updating or not self.metadata_slots.acquire(blocking=False):
                raise ValueError('正在更新或解析其他视频，请稍后再试')
        try:
            started = time.perf_counter()
            result = subprocess.run(self.base_command(s) + ['--dump-single-json', '--skip-download', '--', url],
                                    capture_output=True, encoding='utf-8', errors='replace',
                                    creationflags=NO_WINDOW, timeout=100)
            if result.returncode:
                _, message = classify_error(result.stderr)
                raise ValueError(message + '\n' + self.redact(result.stderr[-1500:]))
            info = json.loads(result.stdout)
            if info.get('entries') is not None or not info.get('formats'):
                raise ValueError('此链接不是可下载的单条视频，请选择具体视频或分P')
            if info.get('is_live'):
                raise ValueError('第一版暂不支持正在直播的视频，请等待直播结束')
            formats = info.get('formats', [])
            if source['platform']=='douyin':
                formats=[f for f in formats if 'download' not in str(f.get('format_id','')).lower()
                    and 'watermark' not in str(f.get('format_note','')).lower()]
                if not formats:
                    raise ValueError('此视频只提供带水印下载流，尚未取得官网播放流。请重新连接抖音后解析。')
                info['formats']=formats
            heights = sorted({int(f['height']) for f in formats if f.get('height') and f.get('vcodec') != 'none'})
            fps = {h: max((f.get('fps') or 0 for f in formats if f.get('height') == h), default=0) for h in heights}
            data = dict(id=vid, url=url, platform=source['platform'], platform_name=NAMES[source['platform']], part=source['part'], title=info.get('title') or vid, duration=info.get('duration') or 0,
                        channel=info.get('uploader') or '', thumbnail=info.get('thumbnail') or '',
                        qualities=[dict(height=h, fps=fps[h]) for h in heights],
                        chapters=[dict(title=x.get('title',''), start=x.get('start_time',0), end=x.get('end_time',0)) for x in info.get('chapters') or []],
                        elapsed_ms=round((time.perf_counter()-started)*1000),cache_hit=False)
            data['preview']=self.register_preview(info,s) if source['platform']!='youtube' else None
            data['parts'],data['parts_error']=fetch_parts(source,s) if source['platform']=='bilibili' else ([], '')
            with self.lock:
                if len(self.metadata_cache) > 50:
                    self.metadata_cache.clear()
                self.metadata_cache[self.cache_key(url,s)] = (time.time(), data, info)
            return data
        except subprocess.TimeoutExpired:
            raise ValueError('解析超时，请检查代理和网络设置') from None
        finally:
            self.metadata_slots.release()

    @staticmethod
    def cache_key(url, s):
        cookie_stamp = Path(s['cookies']).stat().st_mtime_ns if s['cookies'] and Path(s['cookies']).is_file() else 0
        return (url,s['proxy'],s['cookies'],cookie_stamp)

    def register_preview(self, info, s):
        formats=[dict(f,http_headers={**info.get('http_headers',{}),**f.get('http_headers',{})}) for f in info.get('formats',[]) if f.get('url') and f.get('ext') in ('mp4','m4a') and not f.get('fragments') and f.get('protocol') in (None,'http','https')]
        videos=[f for f in formats if (f.get('vcodec') or '').startswith(('avc','h264'))]
        if not videos: return None
        # Preview prefers a moderate AVC stream; download quality is independent.
        below=[f for f in videos if 0<(f.get('height') or 0)<=720]
        chosen=max(below or videos,key=lambda f:f.get('height') or 0) if below else min(videos,key=lambda f:f.get('height') or 99999)
        audio=None
        if chosen.get('acodec')=='none':
            audios=[f for f in formats if f.get('vcodec')=='none' and (f.get('acodec') or '').startswith(('mp4a','aac'))]
            if not audios: return None
            audio=max(audios,key=lambda f:f.get('abr') or f.get('tbr') or 0)
        now=time.time()
        with self.lock:
            self.preview_sources={k:v for k,v in self.preview_sources.items() if now-v['created']<900}
            if len(self.preview_sources)>100: self.preview_sources.clear()
            ids=[]
            for fmt in [chosen]+([audio] if audio else []):
                key=uuid.uuid4().hex
                self.preview_sources[key]=dict(format=fmt,settings=dict(s),created=now)
                ids.append(key)
        return dict(video=ids[0],audio=ids[1] if audio else None)

    def preview_source(self, key):
        with self.lock:
            entry=self.preview_sources.get(key)
            if not entry or time.time()-entry['created']>=900:
                raise ValueError('预览地址已过期，请重新解析')
            return entry

    def cached_download_info(self, url, s):
        with self.lock:
            hit = self.metadata_cache.get(self.cache_key(url,s))
            if not hit or len(hit)<3 or time.time()-hit[0]>=300:
                return None
            # Do not reuse URLs near their server-provided expiration.
            for fmt in hit[2].get('formats',[]):
                expires=urllib.parse.parse_qs(urllib.parse.urlparse(fmt.get('url','')).query).get('expire')
                if expires and expires[0].isdigit() and int(expires[0]) < time.time()+60:
                    return None
            info=dict(hit[2])
            # Re-select formats with the current quality; extraction's default
            # requested formats must not pin a different resolution.
            for key in ('requested_downloads','requested_formats','format','format_id','url','ext','protocol',
                        'width','height','fps','vcodec','acodec','filesize','filesize_approx'):
                info.pop(key,None)
            return info

    def add_jobs(self, data):
        source=parse_source(data.get('url'))
        url, vid = video_url(data.get('url'))
        clips = data.get('clips')
        full = data.get('full') is True
        if full:
            clips=[dict(start=0,end=data.get('duration') or 0,name='完整视频',
                        output_path=data.get('output_path'))]
        if not isinstance(clips, list) or not 1 <= len(clips) <= 100:
            raise ValueError('请添加 1 至 100 个片段')
        s = self.settings()
        output_dir = Path(str(data.get('output_dir') or s['output_dir'])).expanduser()
        if not output_dir.is_absolute():
            raise ValueError('保存目录必须是绝对路径')
        output_dir.mkdir(parents=True,exist_ok=True)
        output_dir = str(output_dir.resolve())
        quality = str(data.get('quality', s['quality']))
        if quality != 'audio' and quality != 'best' and not (quality.isdigit() and 1 <= int(quality) <= 4320):
            raise ValueError('清晰度无效')
        mode = data.get('mode', s['mode'])
        preset = data.get('preset', s['preset'])
        if mode not in ('fast', 'precise') or preset not in ('compatible', 'original'):
            raise ValueError('切割或输出模式无效')
        payloads = []
        for clip in clips:
            shot={}
            if clip.get('shot_cache'):
                if full or quality=='audio': raise ValueError('镜头选区目前用于视频片段导出。')
                record,a,b=self.scenes.selection(clip['shot_cache'],clip.get('shot_start_frame'),clip.get('shot_end_frame'))
                if record['url']!=url or record['quality']!=quality: raise ValueError('分析用清晰度或视频已改变，请保持原清晰度或重新识别镜头。')
                clip=dict(clip,start=a,end=b)
                shot={k:clip[k] for k in ('shot_cache','shot_start_frame','shot_end_frame')}
            start, end = seconds(clip.get('start')), seconds(clip.get('end'))
            if not full and (end <= start or end - start < .1 and not shot):
                raise ValueError('终点必须晚于起点，片段至少为 0.1 秒')
            if end > 7 * 24 * 3600:
                raise ValueError('片段时间超出范围')
            file_path=clip.get('output_path')
            chosen_dir=output_dir
            filename=''
            if file_path:
                target=output_target(file_path,quality,preset)
                chosen_dir=str(target.parent.resolve())
                filename=target.name
            payloads.append(dict(url=url, video_id=vid, platform=source['platform'], part=source['part'], title=str(data.get('title', vid))[:250],
                                 name=str(clip.get('name') or '片段')[:100], start=start, end=end,
                                 quality=quality, mode=mode, preset=preset, output_dir=chosen_dir,
                                 full=full, filename=filename,**shot))
            if shot: payloads[-1]['mode']='precise'
        added, duplicates = [], 0
        with self.lock, self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for p in payloads:
                identity={k:p[k] for k in ('url','start','end','quality','mode','preset','output_dir','full','filename')}
                if p.get('shot_cache'): identity.update({k:p[k] for k in ('shot_cache','shot_start_frame','shot_end_frame')})
                fp = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                if c.execute("SELECT 1 FROM jobs WHERE fingerprint=? AND state NOT IN ('failed','cancelled')", (fp,)).fetchone():
                    duplicates += 1
                    continue
                job_id = uuid.uuid4().hex
                c.execute('INSERT INTO jobs (id,fingerprint,payload,state,created,updated,message) VALUES (?,?,?,?,?,?,?)',
                          (job_id, fp, json.dumps(p, ensure_ascii=False), 'queued', time.time(), time.time(), '等待下载'))
                added.append(job_id)
                c.execute('UPDATE jobs SET queue_rank=created WHERE id=?',(job_id,))
            if added:
                preferences={k:payloads[0][k] for k in ('quality','mode','preset')}
                preferences['last_output_dir']=payloads[-1]['output_dir']
                c.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',[(k,json.dumps(v)) for k,v in preferences.items()])
        self.wake.set()
        return dict(added=added, duplicates=duplicates)

    def jobs(self):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY CASE WHEN state IN ('downloading','processing') THEN 0 WHEN state IN ('queued','retrying','paused') THEN 1 ELSE 2 END, CASE WHEN state IN ('queued','retrying','paused') THEN queue_rank ELSE -created END LIMIT 500").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d['payload'] = json.loads(d['payload'])
            d['metrics']=json.loads(d['metrics'] or '{}')
            d.pop('fingerprint', None)
            result.append(d)
        return result

    def media_path(self, job_id):
        with self.connect() as c:
            row=c.execute("SELECT output FROM jobs WHERE id=? AND state='complete'",(job_id,)).fetchone()
        if not row or not row['output'] or not Path(row['output']).is_file():
            raise ValueError('文件不存在或下载尚未完成')
        return Path(row['output'])

    def patch(self, job_id, **values):
        values['updated'] = time.time()
        with self.lock, self.connect() as c:
            c.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', (*values.values(), job_id))

    def action(self, job_id, action, path=None):
        with self.lock, self.connect() as c:
            row = c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('任务不存在')
            if action=='relocate':
                if row['state'] not in ('failed','cancelled','paused') or job_id in self.processes or job_id in self.executing:
                    raise ValueError('请先等待任务停止，再修改保存位置')
                payload=json.loads(row['payload'])
                target=output_target(path,payload['quality'],payload['preset'])
                payload.update(output_dir=str(target.parent),filename=target.name)
                fingerprint=hashlib.sha256(json.dumps({k:payload.get(k,False if k=='full' else '') for k in ('url','start','end','quality','mode','preset','output_dir','full','filename','shot_cache','shot_start_frame','shot_end_frame')},sort_keys=True).encode()).hexdigest()
                self.patch(job_id,payload=json.dumps(payload,ensure_ascii=False),fingerprint=fingerprint,message='保存位置已更改，可重试或继续下载')
            elif action in ('up','down','first'):
                if row['state'] not in ('queued','retrying','paused'):
                    raise ValueError('只能调整等待或暂停任务的顺序')
                if action=='first':
                    rank=c.execute("SELECT MIN(queue_rank) FROM jobs WHERE state IN ('queued','retrying','paused')").fetchone()[0] or 0
                    self.patch(job_id,queue_rank=rank-1)
                else:
                    direction,order=('<','DESC') if action=='up' else ('>','ASC')
                    other=c.execute(f"SELECT id,queue_rank FROM jobs WHERE state IN ('queued','retrying','paused') AND queue_rank{direction}? ORDER BY queue_rank {order} LIMIT 1",(row['queue_rank'],)).fetchone()
                    if other:
                        self.patch(job_id,queue_rank=other['queue_rank'])
                        self.patch(other['id'],queue_rank=row['queue_rank'])
            elif action=='pause':
                if row['state'] not in ('queued','retrying','downloading','processing'): raise ValueError('当前任务不能暂停')
                self.paused.add(job_id)
                p=json.loads(row['payload'])
                self.patch(job_id,state='paused',message='已暂停；完整视频尽量续传' if p.get('full') else '已暂停；继续时重新下载所选片段',
                    attempt=max(0,row['attempt']-int(row['state'] in ACTIVE)),metrics='{}')
                if job_id in self.processes: self.kill(self.processes[job_id])
            elif action=='resume':
                if row['state']!='paused': raise ValueError('任务未暂停')
                if job_id in self.processes or job_id in self.executing: raise ValueError('正在停止下载，请稍后继续')
                self.paused.discard(job_id)
                self.patch(job_id,state='queued',due=0,message='等待继续下载')
                self.wake.set()
            elif action == 'cancel':
                if row['state'] in ('complete', 'failed', 'cancelled'):
                    raise ValueError('任务已经结束')
                self.cancelled.add(job_id)
                self.paused.discard(job_id)
                self.patch(job_id, state='cancelled', message='已取消')
                process = self.processes.get(job_id)
                if process:
                    self.kill(process)
            elif action == 'retry':
                if row['state'] not in ('failed', 'cancelled', 'retrying') or job_id in self.processes or job_id in self.executing:
                    raise ValueError('请等待当前任务停止后再重试')
                self.cancelled.discard(job_id)
                self.patch(job_id, state='queued', attempt=0, progress=0, due=0, detail='', failure_kind='',metrics='{}',message='等待重新下载')
                self.wake.set()
            elif action in ('open', 'system-play', 'folder'):
                output = Path(row['output']) if row['output'] else None
                if action in ('open', 'system-play'):
                    if not output or not output.is_file() or row['state'] != 'complete':
                        raise ValueError('文件不存在或下载尚未完成')
                    if action == 'system-play':
                        if self.native is None:
                            raise ValueError('Windows 文件操作不可用')
                        return self.native.play(output)
                    return dict(ok=True,media=job_id)
                else:
                    folder = output.parent if output else Path(json.loads(row['payload'])['output_dir'])
                    if not folder.is_dir():
                        raise ValueError('保存目录不存在')
                    if self.native is None:
                        raise ValueError('Windows 文件操作不可用，请从启动程序打开应用。')
                    return self.native.reveal(output if output and output.is_file() else folder)
            else:
                raise ValueError('操作无效')
        return dict(ok=True)

    @staticmethod
    def kill(p):
        if p.poll() is not None:
            return
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True, creationflags=NO_WINDOW)
        else:
            p.terminate()

    def command_for(self, job_id, payload, s):
        # Each attempt re-extracts fresh media URLs. The range goes directly to FFmpeg;
        # no fallback to downloading the entire video is implemented.
        p = payload
        work = self.data / 'jobs' / job_id
        work.mkdir(parents=True, exist_ok=True)
        if p.get('shot_cache'):
            return self.scenes.export_command(p,work/'media.mkv')
        s=platform_settings(s,parse_source(p['url'])['platform'])
        cmd = self.base_command(s) + ['--newline', '--no-quiet', '--no-progress', '--progress',
              '--progress-template', 'download:PROGRESS:{"downloaded_bytes":%(progress.downloaded_bytes)j,"total_bytes":%(progress.total_bytes)j,"total_bytes_estimate":%(progress.total_bytes_estimate)j,"speed":%(progress.speed)j,"eta":%(progress.eta)j}',
              '--print', 'after_move:FILE:%(filepath)s', '--retries', '2', '--fragment-retries', '2',
              '--no-keep-video',
              '--continue' if p.get('full') else '--force-overwrites',
              '--paths', str(work), '--paths', f'temp:{work / "temp"}', '--windows-filenames',
              '--trim-filenames', '160', '--output', 'media.%(ext)s']
        if not p.get('full'):
            cmd += ['--download-sections', f'*{p["start"]}-{p["end"]}']
        q = p['quality']
        bound = '' if q == 'best' else f'[height<={q}]'
        if p.get('platform')=='douyin' and q!='audio':
            bound += '[format_id!*=download][format_note!*=watermark]'
        if q == 'audio':
            cmd += ['-f', 'bestaudio[ext=m4a]/bestaudio/best', '-x', '--audio-format', 'm4a']
        else:
            cmd += ['-f', f'bestvideo{bound}+bestaudio/best{bound}', '-S', 'res,vcodec:h264,acodec:aac', '--merge-output-format', 'mkv']
        ffargs = '-progress pipe:1 -nostats'
        ca = Path(os.environ.get('SSL_CERT_FILE') or self.tools/'yt-dlp'/'_internal'/'certifi'/'cacert.pem')
        if not ca.is_file():
            ca = self.tools/'yt-dlp'/'_internal'/'certifi'/'cacert.pem'
        if ca.is_file():
            cmd += ['--downloader-args', 'ffmpeg_i:-tls_verify 1 -ca_file '+shlex.quote(ca.as_posix())+' -rw_timeout 20000000']
        if p['mode'] == 'precise' and not p.get('full'):
            # FFmpeg's downloader performs the cut, not a postprocessor. Matroska
            # accepts AVC/AAC even when source extension was WebM; final packaging
            # below validates the codecs and produces the requested container.
            cmd += ['--force-keyframes-at-cuts']
            ffargs += ' -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -f matroska'
        cmd += ['--downloader-args', 'ffmpeg_o:'+ffargs]
        return cmd + ['--', p['url']]

    def worker(self, number):
        while not self.stopping.is_set():
            self.wake.wait(.2)
            self.wake.clear()
            try:
                with self.lock, self.connect() as c:
                    prefs=self.settings()
                    if self.updating or prefs['queue_paused'] or number >= prefs['concurrency']:
                        continue
                    c.execute('BEGIN IMMEDIATE')
                    row = c.execute("SELECT * FROM jobs WHERE state IN ('queued','retrying') AND due<=? ORDER BY queue_rank,created LIMIT 1", (time.time(),)).fetchone()
                    if not row:
                        continue
                    c.execute("UPDATE jobs SET state='downloading',attempt=attempt+1,progress=0,message='连接并解析视频',updated=? WHERE id=?", (time.time(), row['id']))
                    self.executing.add(row['id'])
                try:
                    self.execute(row['id'], json.loads(row['payload']), row['attempt'] + 1)
                finally:
                    with self.lock: self.executing.discard(row['id'])
                    self.wake.set()
            except Exception as e:
                if 'row' in locals() and row:
                    self.patch(row['id'], state='failed', message='任务执行异常', detail=self.redact(str(e)))

    def redact(self, text):
        # Logs never persist signed URLs, proxy credentials or cookies contents.
        return re.sub(r'https?://\S+', '[URL]', str(text))[-5000:]

    def interrupted(self, job_id):
        return self.stopping.is_set() or job_id in self.cancelled or job_id in self.paused

    def progress_line(self, job_id, line):
        try:
            raw=json.loads(line[9:])
            metrics={k:float(v) for k,v in raw.items() if k in ('downloaded_bytes','total_bytes','total_bytes_estimate','speed','eta')
                and isinstance(v,(int,float)) and math.isfinite(v) and v>=0}
            total=metrics.get('total_bytes') or metrics.get('total_bytes_estimate') or 0
            progress=min(99,metrics.get('downloaded_bytes',0)/total*100) if total else 0
            self.patch(job_id,progress=progress,metrics=json.dumps(metrics),message='正在下载')
        except (ValueError,TypeError,AttributeError):
            m=re.search(r'([\d.]+)%',line)
            self.patch(job_id,progress=min(99,float(m[1])) if m else 0,message=line[9:].replace('|',' · '))

    def execute(self, job_id, payload, attempt, force_fresh=False):
        p = None
        settings = None
        cached = None
        lines = []
        output = None
        started=time.monotonic()
        processed=0
        try:
            settings = platform_settings(self.settings(),parse_source(payload['url'])['platform'])
            command = self.command_for(job_id, payload, settings)
            if payload.get('shot_cache'):
                output=self.data/'jobs'/job_id/'media.mkv'
                self.patch(job_id,message='从分析缓存逐帧精确导出')
            cached = self.cached_download_info(payload['url'],settings) if attempt==1 and not force_fresh and not payload.get('shot_cache') else None
            if cached:
                cache_file=self.data/'jobs'/job_id/'source-info.json'
                cache_file.write_text(json.dumps(cached,ensure_ascii=False),encoding='utf-8')
                command=command[:-2]+['--load-info-json',str(cache_file)]
                self.patch(job_id,message='复用已解析信息，开始下载')
            with self.lock:
                if self.interrupted(job_id):
                    return
                p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
                                     env={**os.environ, 'TEMP':str(self.data), 'TMP':str(self.data), 'DENO_DIR':str(self.data / 'deno-cache')})
                self.processes[job_id] = p
            for raw in p.stdout:
                if self.interrupted(job_id):
                    self.kill(p)
                    break
                line = raw.strip()
                lines.append(line)
                lines = lines[-60:]
                if line.startswith('PROGRESS:'):
                    self.progress_line(job_id,line)
                elif line.startswith('total_size='):
                    try: processed=max(0,int(line.split('=',1)[1]))
                    except ValueError: pass
                elif line.startswith('out_time_us='):
                    try:
                        elapsed = max(0, int(line.split('=',1)[1]) / 1000000)
                        progress = min(95, elapsed / max(.1,payload['end'] - payload['start']) * 95)
                        wall=max(.1,time.monotonic()-started)
                        eta=max(0,(payload['end']-payload['start']-elapsed)*wall/elapsed) if elapsed>0 else 0
                        self.patch(job_id, progress=progress, metrics=json.dumps(dict(processed_bytes=processed,speed=processed/wall,eta=eta,estimated=True)),message='正在下载完整视频' if payload.get('full') else '正在下载并处理片段')
                    except ValueError:
                        pass
                elif line.startswith('FILE:'):
                    output = Path(line[5:])
                elif any(x in line for x in ('[Merger]', '[VideoConvertor]', '[ExtractAudio]')):
                    self.patch(job_id, state='processing', message='正在合并或转换媒体')
                elif '[download] Destination:' in line:
                    self.patch(job_id, message='正在下载完整视频' if payload.get('full') else '正在下载所选片段')
            code = p.wait()
            p.stdout.close()
            if self.interrupted(job_id):
                return
            if code != 0 or not output or not output.is_file():
                raise RuntimeError('\n'.join(lines) or '没有生成媒体文件')
            self.patch(job_id, state='processing', message='检查媒体文件')
            probe = subprocess.run([str(self.tools / 'ffprobe.exe'), '-v', 'error', '-show_entries',
                                    'format=duration:stream=codec_type,codec_name', '-of', 'json', str(output)],
                                   capture_output=True, encoding='utf-8', creationflags=NO_WINDOW, timeout=30)
            info = json.loads(probe.stdout) if probe.returncode == 0 else {}
            streams = info.get('streams', [])
            duration = float(info.get('format', {}).get('duration', 0))
            if duration <= 0 or not streams:
                raise RuntimeError('输出媒体检查失败：文件无法播放')
            if payload['quality'] != 'audio' and not any(x.get('codec_type') == 'video' for x in streams):
                raise RuntimeError('输出文件缺少画面')
            if payload['quality'] != 'audio':
                self.patch(job_id, state='processing', message='正在生成最终文件', progress=96)
                compatible = payload['preset'] == 'compatible'
                converted = output.with_name('final.mp4' if compatible else 'final.mkv')
                codecs_ok = all(x.get('codec_name') in ('h264','aac') for x in streams if x.get('codec_type') in ('video','audio'))
                codec_args = ['-c','copy'] if not compatible or codecs_ok else ['-c:v','libx264','-preset','fast','-crf','18','-c:a','aac','-b:a','192k']
                convert_cmd = [str(self.tools/'ffmpeg.exe'), '-hide_banner','-loglevel','error','-y','-i',str(output), *codec_args]
                if compatible:
                    convert_cmd += ['-movflags','+faststart']
                convert_cmd += [str(converted)]
                with self.lock:
                    if self.interrupted(job_id):
                        return
                    p = subprocess.Popen(convert_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
                    self.processes[job_id] = p
                log, _ = p.communicate()
                if self.interrupted(job_id):
                    return
                if p.returncode or not converted.is_file():
                    raise RuntimeError(log or '生成最终文件失败')
                output.unlink()
                output = converted
                check = subprocess.run([str(self.tools/'ffprobe.exe'), '-v','error','-show_entries','format=duration:stream=codec_type,codec_name','-of','json',str(output)], capture_output=True, encoding='utf-8', creationflags=NO_WINDOW, timeout=30)
                checked = json.loads(check.stdout) if check.returncode == 0 else {}
                duration = float(checked.get('format',{}).get('duration',0))
                if duration <= 0 or not checked.get('streams'):
                    raise RuntimeError('最终文件检查失败')
            if not payload.get('full') and payload['mode'] == 'precise' and abs(duration-(payload['end']-payload['start'])) > 1:
                raise RuntimeError('精确片段时长检查失败，请重新尝试')
            if payload.get('shot_cache'):
                count=subprocess.run([str(self.tools/'ffprobe.exe'),'-v','error','-select_streams','v:0','-count_frames',
                    '-show_entries','stream=nb_read_frames','-of','json',str(output)],capture_output=True,encoding='utf-8',creationflags=NO_WINDOW,timeout=60)
                counted=json.loads(count.stdout) if count.returncode==0 else {}
                if int(counted.get('streams',[{}])[0].get('nb_read_frames',0))!=payload['shot_end_frame']-payload['shot_start_frame']:
                    raise RuntimeError('镜头导出帧数检查失败，请重新识别后再试。')
            destination = Path(payload['output_dir'])
            destination.mkdir(parents=True, exist_ok=True)
            name = safe_name(payload['title']) + '__' + safe_name(payload['name']) + f'__{payload["start"]:g}-{payload["end"]:g}__{job_id[:6]}'
            final = destination / (payload.get('filename') or name + output.suffix)
            # os.replace is atomic when possible; shutil.move also supports another drive.
            import shutil
            with self.lock:
                if self.interrupted(job_id):
                    return
                base=final
                number=2
                while final.exists():
                    final=base.with_name(base.stem+f' ({number})'+base.suffix)
                    number+=1
                shutil.move(str(output), str(final))
                sidecar = dict(source=payload['url'], title=payload['title'], start=payload['start'], end=payload['end'],
                               quality=payload['quality'], mode=payload['mode'], actual_duration=duration)
                try:
                    final.with_suffix(final.suffix + '.source.json').write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding='utf-8')
                except OSError:
                    pass
                self.patch(job_id, state='complete', progress=100, output=str(final), message='下载完成', detail='',failure_kind='',metrics=json.dumps(dict(downloaded_bytes=final.stat().st_size)))
            work = self.data / 'jobs' / job_id
            if work.is_dir() and work.resolve().is_relative_to((self.data/'jobs').resolve()):
                shutil.rmtree(work, ignore_errors=True)
        except Exception as e:
            if not self.interrupted(job_id):
                raw = str(e)
                if settings is not None:
                    with self.lock:
                        try:
                            self.metadata_cache.pop(self.cache_key(payload['url'],settings),None)
                        except OSError:
                            pass
                retry, message = classify_error(raw)
                if cached and retry and not self.interrupted(job_id):
                    self.patch(job_id,progress=0,message='缓存链接不可用，立即刷新后下载')
                    self.execute(job_id,payload,attempt,force_fresh=True)
                    return
                if retry and attempt <= self.settings()['retries']:
                    delay = min(300, 10 * 2 ** (attempt - 1))
                    self.patch(job_id, state='retrying', due=time.time()+delay, message=f'{delay} 秒后重试（每次重新解析链接）', detail=self.redact(raw),failure_kind=failure_kind(raw),metrics='{}')
                else:
                    self.patch(job_id, state='failed', message=message if not retry else '重试次数已用完，可手动重试', detail=self.redact(raw),failure_kind=failure_kind(raw),metrics='{}')
        finally:
            if p and p.poll() is None:
                self.kill(p)
                p.wait(timeout=10)
            if p and p.stdout and not p.stdout.closed:
                p.stdout.close()
            with self.lock:
                self.processes.pop(job_id, None)

    def stop(self):
        self.stopping.set()
        self.scenes.stop()
        self.wake.set()
        with self.lock:
            for p in list(self.processes.values()):
                self.kill(p)
        for t in self.threads:
            t.join(timeout=5)
        with self.connect() as c:
            c.execute("UPDATE jobs SET state='queued',message='等待下次启动恢复' WHERE state IN ('downloading','processing')")

    def status(self):
        paths = dict(yt_dlp=self.tools/'yt-dlp'/'yt-dlp.exe', ffmpeg=self.tools/'ffmpeg.exe', ffprobe=self.tools/'ffprobe.exe', deno=self.tools/'deno.exe')
        return dict(tools={name:path.is_file() for name,path in paths.items()},
                    root=str(self.root), updating=self.updating)

    def update_engine(self):
        with self.lock, self.connect() as c:
            if self.updating or self.scenes.busy() or c.execute("SELECT 1 FROM jobs WHERE state IN ('queued','retrying','downloading','processing')").fetchone():
                raise ValueError('请等所有下载任务结束或取消后，再更新引擎')
            if not self.metadata_slots.acquire(blocking=False):
                raise ValueError('正在解析视频，请稍后再更新')
            if not self.metadata_slots.acquire(blocking=False):
                self.metadata_slots.release()
                raise ValueError('正在解析视频，请稍后再更新')
            self.updating = True
        try:
            import shutil
            import tempfile
            import urllib.request
            import zipfile
            base = 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/'
            with tempfile.TemporaryDirectory(dir=self.data) as temp:
                temp = Path(temp)
                sums = urllib.request.urlopen(base+'SHA2-256SUMS', timeout=30).read().decode()
                expected = next((line.split()[0] for line in sums.splitlines() if line.split()[-1].lstrip('*') == 'yt-dlp_win.zip'), None)
                archive = temp/'engine.zip'
                with urllib.request.urlopen(base+'yt-dlp_win.zip', timeout=60) as response, archive.open('wb') as target:
                    shutil.copyfileobj(response,target)
                if not expected or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                    raise ValueError('更新包校验失败，保留现有引擎')
                new = temp/'new'
                with zipfile.ZipFile(archive) as z:
                    for member in z.namelist():
                        resolved = (new/member).resolve()
                        if not resolved.is_relative_to(new.resolve()):
                            raise ValueError('更新包路径无效')
                    z.extractall(new)
                check = subprocess.run([str(new/'yt-dlp.exe'),'--version'],capture_output=True,encoding='utf-8',creationflags=NO_WINDOW,timeout=30)
                if check.returncode:
                    raise ValueError('新版引擎不能运行，保留现有引擎')
                current = self.tools/'yt-dlp'
                backup = temp/'old'
                current.rename(backup)
                try:
                    new.rename(current)
                except Exception:
                    backup.rename(current)
                    raise
            self.metadata_cache.clear()
            return dict(message='引擎已更新：'+check.stdout.strip())
        except subprocess.TimeoutExpired:
            raise ValueError('更新超时，请稍后重试') from None
        finally:
            self.updating = False
            self.metadata_slots.release()
            self.metadata_slots.release()
