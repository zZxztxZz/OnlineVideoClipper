"""Local shot selection, decoded frame timestamps and frame-index export."""
import bisect
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import threading
import time
import uuid
from sources import parse_source, platform_settings
from audio_tracks import track_id, audio_selector


class SceneManager:
    def __init__(self, engine):
        self.engine=engine
        self.root=engine.data/'shot-cache'
        self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock()
        self.slot=threading.Lock()
        self.picture_lock=threading.Lock()
        self.stopped=threading.Event()
        self.processes=set()
        self.tasks={}
        self.player_pins=set()
        self.prune()

    def prune(self):
        with self.engine.connect() as c:
            pinned=set(self.player_pins)
            for r in c.execute("SELECT payload FROM jobs WHERE state IN ('queued','retrying','paused','downloading','processing')"):
                payload=json.loads(r['payload']);pinned.add(payload.get('shot_cache'))
                pinned.update(x['id'] for x in payload.get('player_segments',[]))
        with self.lock: pinned.update(k for k,t in self.tasks.items() if t['state']=='working')
        directories=[]
        for p in self.root.iterdir():
            if p.is_dir() and re.fullmatch(r'[0-9a-f]{32}',p.name) and p.resolve().is_relative_to(self.root.resolve()):
                size=sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
                directories.append((p.stat().st_mtime,size,p))
        total=sum(x[1] for x in directories)
        for stamp,size,p in sorted(directories):
            if p.name not in pinned and (time.time()-stamp>86400 or total>2*1024**3):
                shutil.rmtree(p,ignore_errors=True)
                total-=size

    def start(self, url, center, quality, duration, radius=12, mode='scene', begin=None, finish=None, preview=True,audio_track=''):
        if self.stopped.is_set(): raise ValueError('程序正在退出')
        source=parse_source(url)
        audio_track=track_id(audio_track)
        if source['short']: raise ValueError('请先解析视频，再识别镜头。')
        if quality=='audio' or quality!='best' and not (str(quality).isdigit() and 1<=int(quality)<=4320):
            raise ValueError('识别镜头需要视频清晰度，请先选择视频格式。')
        center=float(center);duration=float(duration);radius=int(radius)
        if not all(math.isfinite(x) for x in (center,duration)) or not 0<=center<duration or not 0<duration<=604800:
            raise ValueError('定位时间超出视频范围，请先解析有效的视频时长。')
        if radius not in (12,30,60): raise ValueError('搜索范围无效')
        if mode not in ('scene','manual'): raise ValueError('准备方式无效')
        if mode=='manual':
            begin=float(begin);finish=float(finish)
            if not all(math.isfinite(x) for x in (begin,finish)) or not 0<=begin<finish<=duration or not begin<=center<finish:
                raise ValueError('请检查选区和定位时间。')
            if finish-begin>600: raise ValueError('逐帧微调每次支持 10 分钟以内的选区，请先缩短选区。')
        if not self.slot.acquire(blocking=False): raise ValueError('正在识别镜头，请先等待完成或取消。')
        key=uuid.uuid4().hex
        task=dict(id=key,url=source['url'],center=center,quality=str(quality),duration=duration,radius=radius,audio_track=audio_track,
                  state='working',message='正在准备逐帧预览…' if mode=='manual' else '正在读取附近视频…',
                  mode=mode,begin=begin,finish=finish,preview=preview,cancel=threading.Event(),process=None)
        with self.lock: self.tasks[key]=task
        task['thread']=threading.Thread(target=self.work,args=(task,),daemon=True)
        task['thread'].start()
        return dict(id=key)

    def run(self, args, task, timeout=180):
        with self.lock:
            if task['cancel'].is_set() or self.stopped.is_set(): raise ValueError('已取消镜头识别')
            p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env={**os.environ,'TEMP':str(self.engine.data),'TMP':str(self.engine.data),'DENO_DIR':str(self.engine.data/'deno-cache')})
            task['process']=p
            self.processes.add(p)
        timed_out=threading.Event()
        def expire():
            timed_out.set();self.engine.kill(p)
        timer=threading.Timer(timeout,expire)
        timer.daemon=True;timer.start()
        try:
            output=p.communicate()[0]
            if task['cancel'].is_set(): raise ValueError('已取消镜头识别')
            if timed_out.is_set(): raise ValueError('镜头分析超时，可缩小范围后重试。')
            if p.returncode:
                from core import classify_error
                task['detail']=self.engine.redact(output)
                _,message=classify_error(output)
                raise ValueError(message)
            return output
        finally:
            timer.cancel()
            with self.lock:
                self.processes.discard(p)
                if task['process'] is p: task['process']=None

    def download_command(self, task, start, end, directory):
        s=platform_settings(self.engine.settings(),parse_source(task['url'])['platform'])
        bound='' if task['quality']=='best' else '[height<='+task['quality']+']'
        if parse_source(task['url'])['platform']=='douyin': bound+='[format_id!*=download][format_note!*=watermark]'
        cmd=self.engine.base_command(s)+['--newline','--no-quiet','--download-sections',f'*{start}-{end}',
            '-f',f'bestvideo{bound}+{audio_selector(task.get("audio_track",""))}'+(f'/best{bound}' if not task.get('audio_track') else ''),'-S','res,vcodec:h264,acodec:aac',
            '--paths',str(directory),'--output','source.%(ext)s','--force-overwrites','--print','after_move:FILE:%(filepath)s',
            '--downloader-args','ffmpeg_o:-copyts -start_at_zero -avoid_negative_ts disabled -f matroska',
            '--socket-timeout','20','--retries','1','--fragment-retries','1']
        cached=self.engine.cached_download_info(task['url'],s,max_age=7200 if self.engine.preview_origin else 300)
        if cached: cached=self.engine.cached_preview_info(cached,task['quality'],task.get('audio_track',''))
        local=bool(cached and self.engine.preview_origin and all(f.get('url','').startswith(self.engine.preview_origin) for f in cached.get('formats',[])))
        ca=Path(os.environ.get('SSL_CERT_FILE') or self.engine.tools/'yt-dlp'/'_internal'/'certifi'/'cacert.pem')
        if ca.is_file() and not local:
            import shlex
            cmd+=['--downloader-args','ffmpeg_i:-tls_verify 1 -ca_file '+shlex.quote(ca.as_posix())+' -rw_timeout 20000000']
        if local:cmd+=['--downloader-args','ffmpeg_i:-rw_timeout 20000000']
        if cached:
            info=directory/'source-info.json'
            info.write_text(json.dumps(cached,ensure_ascii=False),encoding='utf-8')
            return cmd+['--load-info-json',str(info)]
        return cmd+['--',task['url']]

    def analyze(self, task, media, start, end):
        task['message']='正在读取真实帧时间…' if task.get('mode')=='manual' else '正在分析帧时间…'
        probe=json.loads(self.run([str(self.engine.tools/'ffprobe.exe'),'-v','error','-select_streams','v:0','-show_frames',
            '-show_entries','frame=best_effort_timestamp_time,duration_time,pkt_duration_time:stream=width,height,avg_frame_rate',
            '-of','json',str(media)],task))
        frames=probe.get('frames',[])
        times=[float(f['best_effort_timestamp_time']) for f in frames]
        if len(times)<2 or any(not math.isfinite(x) for x in times) or any(b<=a for a,b in zip(times,times[1:])):
            raise ValueError('该视频帧时间不连续，暂不能可靠地逐帧选镜头。')
        if times[0]>task['center'] or times[-1]>task['duration']+3:
            raise ValueError('附近视频的时间轴无法与源视频对齐，请重新解析后再试。')
        step=statistics.median(b-a for a,b in zip(times,times[1:]))
        last_duration=float(frames[-1].get('duration_time') or frames[-1].get('pkt_duration_time') or step)
        last_end=min(task['duration'],times[-1]+max(step/2,last_duration))
        if last_end<=times[-1]: last_end=times[-1]+step
        if task['center']>=last_end+.0005:
            raise ValueError('定位时间已超出读取到的视频画面，请稍向前定位后重试。')
        cuts=self.detect_cuts(task,media,times) if task.get('mode')!='manual' else []
        edges=[0]+cuts+[len(times)]
        left_known=start==0 and times[0]<=step*1.5
        right_known=end>=task['duration']-.001 and last_end>=task['duration']-max(.5,step*2)
        shots=[dict(start_index=a,end_index=b,left_found=a>0 or left_known,right_found=b<len(times) or right_known) for a,b in zip(edges,edges[1:])]
        selected=next((i for i,s in enumerate(shots) if times[s['start_index']]<=task['center']<(times[s['end_index']] if s['end_index']<len(times) else last_end)),len(shots)-1)
        stream=probe.get('streams',[{}])[0]
        return dict(url=task['url'],quality=task['quality'],audio_track=task.get('audio_track',''),duration=task['duration'],center=task['center'],
            times=times,last_end=last_end,shots=shots,selected_shot=selected,
            width=stream.get('width'),height=stream.get('height'),fps=round(1/step,3),radius=task['radius'],
            cuts=cuts,cuts_ready=task.get('mode')!='manual',mode=task.get('mode','scene'))

    def detect_cuts(self,task,media,times):
        log=self.run([str(self.engine.tools/'ffmpeg.exe'),'-hide_banner','-loglevel','info','-copyts','-i',str(media),
            '-vf','scale=320:-2,scdet=threshold=12,metadata=print','-an','-f','null','-'],task)
        return sorted({i for t in re.findall(r'lavfi\.scd\.time=([\d.]+)',log)
                       if 0<(i:=bisect.bisect_left(times,float(t)-.0005))<len(times)})

    def work(self, task):
        directory=self.root/task['id']
        try:
            directory.mkdir()
            self.prune()
            record=None
            manual=task.get('mode')=='manual'
            for radius in ([task['radius']] if manual else [12,30] if task['radius']==12 else [task['radius']]):
                start=max(0,task['begin']-3) if manual else max(0,task['center']-radius)
                end=min(task['duration'],task['finish']+3) if manual else min(task['duration'],task['center']+radius)
                task['message']='正在读取附近视频…' if radius==task['radius'] else '附近未找到完整边界，自动扩大搜索范围…'
                task['radius']=radius
                output=self.run(self.download_command(task,start,end,directory),task)
                files=re.findall(r'^FILE:(.+)$',output,re.M)
                if not files: raise ValueError('没有取得附近视频，请检查平台连接和网络。')
                media=Path(files[-1].strip())
                if not media.is_file() or not media.resolve().is_relative_to(directory.resolve()): raise ValueError('分析文件无效')
                target=directory/'source.mkv'
                if media!=target: os.replace(media,target)
                record=self.analyze(task,target,start,end)
                selected=record['shots'][record['selected_shot']]
                if manual or selected['left_found'] and selected['right_found'] or start==0 and end==task['duration']: break
            task['message']='正在完成选帧数据…' if not task.get('preview',True) else '正在准备预览…'
            if task.get('preview',True):self.make_preview(task,record,directory/'source.mkv',directory/'preview.mp4')
            record['preview_version']=1 if task.get('preview',True) else 2
            (directory/'analysis.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
            for p in directory.glob('*.json'):
                if p.name!='analysis.json': p.unlink(missing_ok=True)
            task['record']=record
            task['state']='ready';task['message']='镜头识别完成'
        except Exception as ex:
            task['state']='cancelled' if task['cancel'].is_set() else 'failed'
            task['message']=str(ex) if isinstance(ex,ValueError) else '镜头识别失败，请重新解析后重试。'
            if directory.resolve().is_relative_to(self.root.resolve()): shutil.rmtree(directory,ignore_errors=True)
        finally: self.slot.release()

    def make_preview(self,task,record,media,target):
        first=record['times'][0]
        self.run([str(self.engine.tools/'ffmpeg.exe'),'-hide_banner','-loglevel','error','-y','-copyts','-i',str(media),
            '-map','0:v:0','-map','0:a:0?','-vf',r'scale=w=min(720\,iw):h=-2,setpts=PTS-STARTPTS',
            '-af',f'atrim=start={first},asetpts=PTS-STARTPTS','-c:v','libx264','-preset','veryfast','-crf','23',
            '-c:a','aac','-fps_mode','passthrough','-enc_time_base','filter','-movflags','+faststart',str(target)],task)

    def record(self,key):
        if not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{32}',key): raise ValueError('镜头缓存无效')
        task=self.tasks.get(key)
        if task and task.get('state')=='ready': return task['record']
        path=self.root/key/'analysis.json'
        if not path.is_file(): raise ValueError('镜头分析缓存已清理，请重新识别。')
        return json.loads(path.read_text(encoding='utf-8'))

    def status(self,key):
        with self.lock:
            task=self.tasks.get(key)
            if not task: raise ValueError('镜头识别任务不存在')
            data=dict(id=key,state=task['state'],message=task['message'])
            if task['state']=='ready': data.update(task['record'])
            return data

    def cancel(self,key):
        with self.lock:
            task=self.tasks.get(key)
            if task and task['state']=='working':
                task['cancel'].set()
                if task['process']: self.engine.kill(task['process'])
        return dict(ok=True)

    def busy(self):
        return self.slot.locked()

    def stop(self):
        self.stopped.set()
        for key in list(self.tasks): self.cancel(key)
        with self.lock: processes=list(self.processes)
        for p in processes: self.engine.kill(p)
        for task in list(self.tasks.values()):
            if task.get('thread') and task['thread'] is not threading.current_thread(): task['thread'].join(3)

    def selection(self,key,start,end):
        record=self.record(key)
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(record['times']):
            raise ValueError('镜头至少需要保留一帧，边界不能超出已读取范围。')
        a=record['times'][start];b=record['times'][end] if end<len(record['times']) else record['last_end']
        return record,a,b

    def pictures(self,key,start,end):
        with self.picture_lock:
            return self._pictures(key,start,end)

    def _pictures(self,key,start,end):
        record,a,b=self.selection(key,start,end)
        directory=self.root/key
        indices=sorted({i for i in (start-1,start,end-1,end) if 0<=i<len(record['times'])})
        neighbours=sorted({j for i in indices for j in range(max(0,i-3),min(len(record['times']),i+4))})
        missing=[i for i in neighbours if not (directory/f'frame-{i}.jpg').is_file()]
        if missing:
            task=dict(cancel=threading.Event(),process=None)
            groups=[]
            for i in missing:
                if not groups or record['times'][i]-record['times'][groups[-1][-1]]>1: groups.append([])
                groups[-1].append(i)
            for group in groups:
                pattern=directory/('image-'+uuid.uuid4().hex+'-%02d.jpg')
                expression='+'.join(rf'lt(abs(t-{record["times"][i]})\,0.0004)' for i in group)
                seek=max(0,record['times'][group[0]]-record['times'][0]-1)
                self.run([str(self.engine.tools/'ffmpeg.exe'),'-v','error','-y','-ss',str(seek),'-copyts','-i',str(directory/'source.mkv'),
                    '-vf',rf'select={expression},scale=w=min(720\,iw):h=-2','-fps_mode','vfr','-frames:v',str(len(group)),str(pattern)],task,30)
                for j,i in enumerate(group,1):
                    source=Path(str(pattern).replace('%02d',f'{j:02d}'))
                    if not source.is_file(): raise ValueError('边界帧读取失败，请重新识别。')
                    os.replace(source,directory/f'frame-{i}.jpg')
        def url(i): return f'shots/{key}/frame-{i}.jpg' if i in indices else None
        return dict(start=a,end=b,start_before=url(start-1),start_after=url(start),end_before=url(end-1),end_after=url(end))

    def asset(self,key,name):
        self.record(key)
        if name!='preview.mp4' and not re.fullmatch(r'(frame-\d+\.jpg|fullframe-\d+\.png)',name): raise ValueError('文件无效')
        path=self.root/key/name
        if not path.is_file(): raise ValueError('文件不存在')
        return path

    def frame(self,key,index):
        record=self.record(key)
        if type(index) is not int or not 0<=index<len(record['times']): raise ValueError('帧位置无效')
        path=self.full_frame(key,index)
        image=f'shots/{key}/{path.name}'
        return dict(index=index,time=record['times'][index],image=image,width=record['width'],height=record['height'])

    def full_frame(self,key,index):
        record=self.record(key)
        if type(index) is not int or not 0<=index<len(record['times']): raise ValueError('帧位置无效')
        directory=self.root/key
        target=directory/f'fullframe-{index}.png'
        with self.picture_lock:
            if not target.is_file():
                stamp=record['times'][index]
                temporary=directory/('still-'+uuid.uuid4().hex+'.png')
                try:
                    self.run([str(self.engine.tools/'ffmpeg.exe'),'-v','error','-y',
                        '-ss',str(max(0,stamp-record['times'][0]-1)),'-copyts','-i',str(directory/'source.mkv'),
                        '-vf',rf'select=lt(abs(t-{stamp})\,0.0004)','-frames:v','1','-compression_level','1',str(temporary)],
                        dict(cancel=threading.Event(),process=None),60)
                    if not temporary.is_file(): raise ValueError('没有读取到这一帧，请重新准备逐帧预览。')
                    os.replace(temporary,target)
                finally: temporary.unlink(missing_ok=True)
            os.utime(target,None)
            # Display images are disposable; source frame references remain valid.
            images=sorted(directory.glob('fullframe-*.png'),key=lambda p:p.stat().st_mtime,reverse=True)
            total=0
            for n,image in enumerate(images):
                total+=image.stat().st_size
                if image!=target and (n>=48 or total>128*1024*1024): image.unlink(missing_ok=True)
        return target

    def snap(self,key,index,side):
        record=self.record(key)
        if side not in ('start','end') or type(index) is not int or not 0<=index<=len(record['times']):
            raise ValueError('吸附位置无效')
        with self.picture_lock:
            if not record.get('cuts_ready'):
                task=dict(cancel=threading.Event(),process=None)
                record['cuts']=self.detect_cuts(task,self.root/key/'source.mkv',record['times'])
                record['cuts_ready']=True
                (self.root/key/'analysis.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
            candidates=list(record.get('cuts',[]))
            if side=='start' and record['times'][0]<.05: candidates.append(0)
            if side=='end' and record['last_end']>=record['duration']-.05: candidates.append(len(record['times']))
            matches=[i for i in candidates if i<=index] if side=='start' else [i for i in candidates if i>=index]
            return dict(found=bool(matches),index=(max(matches) if side=='start' else min(matches)) if matches else index)

    def snapshot(self,key,index,path):
        record=self.record(key)
        if type(index) is not int or not 0<=index<len(record['times']): raise ValueError('帧位置无效')
        extension=Path(str(path)).suffix.lower()
        if extension not in ('.png','.jpg'): raise ValueError('截图支持 PNG 或 JPG 文件。')
        from core import output_target
        requested=output_target(path,'best','compatible',extension=extension)
        target=requested;number=1
        with self.engine.lock:
            while True:
                try:
                    with target.open('xb'): pass
                    break
                except FileExistsError:
                    number+=1;target=requested.with_name(f'{requested.stem} ({number}){extension}')
        temporary=self.root/key/('still-'+uuid.uuid4().hex+extension)
        try:
            stamp=record['times'][index]
            task=dict(cancel=threading.Event(),process=None)
            self.run([str(self.engine.tools/'ffmpeg.exe'),'-v','error','-y',
                '-ss',str(max(0,stamp-record['times'][0]-1)),'-copyts','-i',str(self.root/key/'source.mkv'),
                '-vf',rf'select=lt(abs(t-{stamp})\,0.0004)','-frames:v','1','-q:v','2',str(temporary)],task,60)
            if not temporary.is_file(): raise ValueError('没有读取到这一帧，请重新准备逐帧预览。')
            shutil.copyfile(temporary,target)
            self.engine.save_settings(dict(last_output_dir=str(target.parent)))
            return dict(path=str(target),time=stamp,width=record['width'],height=record['height'])
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally: temporary.unlink(missing_ok=True)

    def export_command(self,payload,target):
        record,a,b=self.selection(payload['shot_cache'],payload['shot_start_frame'],payload['shot_end_frame'])
        if record['url']!=payload['url'] or record['quality']!=payload['quality'] or record.get('audio_track','')!=payload.get('audio_track',''): raise ValueError('视频或清晰度已改变，请重新识别镜头。')
        return [str(self.engine.tools/'ffmpeg.exe'),'-hide_banner','-loglevel','error','-y','-copyts',
            '-i',str(self.root/payload['shot_cache']/'source.mkv'),'-map','0:v:0','-map','0:a:0?',
            '-vf',f'trim=start_frame={payload["shot_start_frame"]}:end_frame={payload["shot_end_frame"]},setpts=PTS-STARTPTS',
            '-af',f'atrim=start={a}:end={b},asetpts=PTS-STARTPTS','-c:v','libx264','-preset','fast','-crf','18',
            '-c:a','aac','-b:a','192k','-fps_mode','passthrough','-enc_time_base','filter',
            '-progress','pipe:1','-nostats','-f','matroska',str(target)]

    def validate_segments(self,segments,url,quality,audio_track=''):
        if not isinstance(segments,list) or not 1<=len(segments)<=64: raise ValueError('素材缓冲引用无效')
        checked=[];previous=None
        for item in segments:
            if not isinstance(item,dict): raise ValueError('素材缓冲引用无效')
            record,a,b=self.selection(item.get('id'),item.get('start'),item.get('end'))
            if record['url']!=url or record['quality']!=quality or record.get('audio_track','')!=audio_track: raise ValueError('素材视频或清晰度不匹配')
            if previous is not None and abs(a-previous)>.002: raise ValueError('选区缓冲不连续，请重新加载缺失的位置。')
            checked.append((record,a,b));previous=b
        return checked,checked[0][1],checked[-1][2]

    def segments_command(self,payload,target):
        segments=payload['player_segments']
        records,_,_=self.validate_segments(segments,payload['url'],payload['quality'],payload.get('audio_track',''))
        command=[str(self.engine.tools/'ffmpeg.exe'),'-hide_banner','-loglevel','error','-y','-copyts']
        filters=[];labels=[]
        for n,(item,(record,a,b)) in enumerate(zip(segments,records)):
            media=self.root/item['id']/'source.mkv'
            command+=['-i',str(media)]
            probe=json.loads(subprocess.check_output([str(self.engine.tools/'ffprobe.exe'),'-v','error',
                '-show_entries','stream=codec_type','-of','json',str(media)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30))
            audio=any(s['codec_type']=='audio' for s in probe.get('streams',[]))
            filters.append(f'[{n}:v]trim=start_frame={item["start"]}:end_frame={item["end"]},setpts=PTS-STARTPTS[v{n}]')
            if audio:
                filters.append(f'[{n}:a]atrim=start={a}:end={b},asetpts=PTS-{a}/TB,aresample=48000:async=1:first_pts=0,apad,atrim=duration={b-a}[a{n}]')
            else:
                filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={b-a}[a{n}]')
            labels.append(f'[v{n}][a{n}]')
        filters.append(''.join(labels)+f'concat=n={len(segments)}:v=1:a=1[v][a]')
        return command+['-filter_complex',';'.join(filters),'-map','[v]','-map','[a]',
            '-c:v','libx264','-preset','fast','-crf','18','-c:a','aac','-b:a','192k',
            '-fps_mode','passthrough','-enc_time_base','filter','-progress','pipe:1','-nostats','-f','matroska',str(target)]
