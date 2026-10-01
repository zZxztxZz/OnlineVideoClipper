"""Demand-first bounded source windows shared by playback and exact exports."""
import bisect
import json
import math
import threading
import time
from sources import parse_source


class PlayerCache:
    FIRST=8
    BACK=3
    AHEAD=12
    PREFETCH=24
    MAX_SECONDS=120
    EXPORT_SECONDS=600
    MAX_BYTES=512*1024**2

    def __init__(self,engine):
        self.engine=engine
        self.scenes=engine.scenes
        self.lock=threading.RLock()
        self.wake=threading.Event()
        self.stopped=threading.Event()
        self.session=None
        self.serial=0
        self.entries={}
        self.pending=None
        self.running=None
        self.active=None
        self.keep=set()
        self.error=''
        self.last_latency=0
        self.thread=threading.Thread(target=self.work,daemon=True)
        self.thread.start()

    @staticmethod
    def window(t,duration,first=False,prefetch=False):
        begin=max(0,t-(0 if first or prefetch else PlayerCache.BACK))
        end=min(duration,t+(PlayerCache.FIRST if first else PlayerCache.PREFETCH if prefetch else PlayerCache.AHEAD))
        return begin,end

    def request(self,data):
        source=parse_source(data.get('url'))
        quality=str(data.get('quality'))
        duration=float(data.get('duration'));position=float(data.get('time'))
        if source['short'] or quality!='best' and not (quality.isdigit() and 1<=int(quality)<=4320):
            raise ValueError('请先解析视频并选择视频清晰度。')
        if not all(math.isfinite(v) for v in (duration,position)) or not 0<=position<duration<=604800:
            raise ValueError('播放位置无效')
        session=(source['url'],quality,duration)
        prefetch=data.get('prefetch') is True
        with self.lock:
            if self.stopped.is_set(): raise ValueError('程序正在退出')
            if session!=self.session:
                self.cancel_running();self.entries={};self.keep=set();self.active=None;self.pending=None
                self.session=session;self.serial+=1;self.error=''
                self.restore(session)
            protected=data.get('keep',[])
            if not isinstance(protected,list) or len(protected)>64: raise ValueError('缓冲引用无效')
            self.keep={key for key in protected if key in self.entries}
            hit=self.find(position)
            if prefetch:
                if hit or self.running or self.pending: return self.status()
            else:
                self.serial+=1;self.error=''
                # Promote an in-flight window covering the target, rather than throw it away.
                if not hit and self.running and not self.running.get('cancelled') and self.running['session']==session and self.running['begin']<=position<self.running['finish']:
                    self.running['prefetch']=False;self.running['serial']=self.serial;self.pending=None;self.active=None
                    return self.status()
                self.cancel_running();self.pending=None
                if hit:
                    self.active=hit;self.entries[hit]['used']=time.monotonic();self.trim()
                    return self.status()
                self.active=None
            begin,end=self.window(position,duration,first=data.get('first') is True,prefetch=prefetch)
            # Leave capacity for a complete new window; protected exports take precedence.
            budget=self.EXPORT_SECONDS+30 if data.get('export') is True else self.MAX_SECONDS
            if not self.trim(reserve=end-begin+6,budget=budget):
                if not prefetch:self.error='已保留的选区占满缓冲，请下载或清空片段后重试。'
                return self.status()
            self.pending=dict(url=session[0],quality=quality,duration=duration,center=position,
                              begin=begin,finish=end,mode='manual',prefetch=prefetch,serial=self.serial,session=session,budget=budget)
            self.pins();self.wake.set()
            return self.status()

    def cancel_running(self):
        if self.running:
            self.running['cancelled']=True
            if self.running.get('id'): self.scenes.cancel(self.running['id'])

    def restore(self,session):
        for path in self.scenes.root.glob('*/analysis.json'):
            try:
                if time.time()-path.stat().st_mtime>86400:continue
                record=self.scenes.record(path.parent.name)
                if (record['url'],record['quality'],record['duration'])!=session:continue
                if record['last_end']-record['times'][0]>self.MAX_SECONDS:continue
                if not (path.parent/'preview.mp4').is_file() or not (path.parent/'source.mkv').is_file():continue
                self.entries[path.parent.name]=dict(record=record,used=path.stat().st_mtime-time.time()+time.monotonic())
            except (ValueError,OSError,KeyError):continue
        self.pins()

    def find(self,t):
        candidates=[(e['record']['last_end'],key) for key,e in self.entries.items()
                    if e['record']['times'][0]-.0004<=t<e['record']['last_end']-.00001]
        return max(candidates)[1] if candidates else None

    def pins(self):
        self.scenes.player_pins=set(self.entries)

    def job_pins(self):
        with self.engine.connect() as c:
            pinned=set()
            for row in c.execute("SELECT payload FROM jobs WHERE state IN ('queued','retrying','paused','downloading','processing')"):
                p=json.loads(row['payload']);pinned.add(p.get('shot_cache'))
                pinned.update(x['id'] for x in p.get('player_segments',[]))
            return pinned

    def trim(self,reserve=0,budget=None):
        budget=budget or self.MAX_SECONDS
        protected=self.keep|self.job_pins()|({self.active} if self.active else set())
        def totals():
            intervals=sorted((e['record']['times'][0],e['record']['last_end']) for e in self.entries.values())
            seconds=0;right=0
            for begin,end in intervals:
                seconds+=max(0,end-max(begin,right));right=max(right,end)
            return (seconds,
                    sum(sum(p.stat().st_size for p in (self.scenes.root/k).glob('*') if p.is_file()) for k in self.entries))
        seconds,size=totals()
        for key,e in sorted(list(self.entries.items()),key=lambda kv:kv[1]['used']):
            if seconds+reserve<=budget and size<=self.MAX_BYTES:break
            if key in protected:continue
            self.entries.pop(key)
            seconds,size=totals()
        self.pins()
        # Evicted windows become eligible for the shared 2 GiB / 24 h disk cleanup.
        self.scenes.prune()
        return seconds+reserve<=budget and size<=self.MAX_BYTES

    def status(self):
        with self.lock:
            record=self.entries.get(self.active,{}).get('record')
            ranges=[dict(id=k,start=e['record']['times'][0],end=e['record']['last_end']) for k,e in self.entries.items()]
            state='ready' if record else 'failed' if self.error else 'working'
            message=self.error or ('当前位置已缓冲' if record else '正在加载当前位置附近的素材…')
            if not record and self.running and self.running.get('id'):
                message=self.scenes.status(self.running['id'])['message']
            return dict(state=state,message=message,serial=self.serial,id=self.active,record=record,ranges=ranges,
                        prefetch=bool(self.pending or self.running),prefetch_lead=min(30,max(10,self.last_latency*1.5+2)),
                        limits=dict(seconds=self.MAX_SECONDS,export_seconds=self.EXPORT_SECONDS,bytes=self.MAX_BYTES,disk_bytes=2*1024**3))

    def work(self):
        while not self.stopped.is_set():
            self.wake.wait(.2);self.wake.clear()
            with self.lock:
                task=self.pending;self.pending=None
                if not task:continue
                self.running=task
            try:
                started=time.monotonic()
                while self.scenes.busy():
                    if self.stopped.wait(.05) or task.get('cancelled'):break
                if task.get('cancelled') or self.stopped.is_set():continue
                with self.lock:
                    if task.get('cancelled'):continue
                    result=self.scenes.start(**{k:v for k,v in task.items() if k in ('url','quality','duration','center','begin','finish','mode')})
                    task['id']=result['id']
                while not self.stopped.wait(.1):
                    state=self.scenes.status(task['id'])
                    if state['state']!='working':break
                    size=sum(p.stat().st_size for p in (self.scenes.root/task['id']).glob('*') if p.is_file())
                    if size>self.MAX_BYTES:
                        self.scenes.cancel(task['id']);raise ValueError('当前素材超过 512 MiB 缓冲上限，请降低清晰度。')
                if self.stopped.is_set():break
                if task.get('cancelled'):continue
                if state['state']!='ready':raise ValueError(state['message'])
                with self.lock:
                    if task['session']!=self.session:continue
                    self.entries[task['id']]=dict(record=state,used=time.monotonic())
                    self.last_latency=time.monotonic()-started
                    if not task['prefetch']:self.active=task['id']
                    if not self.trim(budget=task['budget']):
                        if not task['prefetch']:self.error='缓冲已达到容量上限，请降低清晰度或缩短选区。';self.active=None
            except Exception as ex:
                with self.lock:
                    if not task.get('cancelled') and not task['prefetch']:self.error=str(ex) if isinstance(ex,ValueError) else '缓冲失败，请重试。'
            finally:
                with self.lock:self.running=None
                if self.pending:self.wake.set()

    def selection(self,data):
        begin=float(data.get('start'));end=float(data.get('end'))
        if not all(math.isfinite(v) for v in (begin,end)) or not 0<=begin<end or end-begin>600:
            raise ValueError('逐帧素材选区支持 10 分钟以内，请缩短选区。')
        with self.lock:
            if not self.session or data.get('url')!=self.session[0] or str(data.get('quality'))!=self.session[1]:
                raise ValueError('视频或清晰度已改变，请重新加载。')
            cursor=begin;segments=[]
            while cursor<end-.0005:
                key=self.find(cursor)
                if not key:return dict(ready=False,gap=cursor,segments=segments)
                record=self.entries[key]['record'];times=record['times']
                a=max(0,bisect.bisect_left(times,cursor-.0004))
                b=bisect.bisect_left(times,min(end,record['last_end'])-.0004)
                if b<=a:return dict(ready=False,gap=cursor,segments=segments)
                segments.append(dict(id=key,start=a,end=b))
                cursor=times[b] if b<len(times) else record['last_end']
                self.entries[key]['used']=time.monotonic()
            self.keep.update(s['id'] for s in segments);self.pins()
            return dict(ready=True,segments=segments)

    def stop(self):
        self.stopped.set();self.cancel_running();self.wake.set();self.thread.join(3)
