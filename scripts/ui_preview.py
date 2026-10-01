import sys, threading, json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
from core import Engine
from server import LocalServer
e=Engine(ROOT/'work'/'ui-preview',workers=False)
e.metadata=lambda url:dict(id='M7lc1UVf-VE',url='https://www.youtube.com/watch?v=M7lc1UVf-VE',title='桌面布局验证 · 视频预览、片段剪辑与下载队列',channel='布局验证示例',duration=1344,qualities=[dict(height=360,fps=30),dict(height=720,fps=30),dict(height=1080,fps=30)],chapters=[dict(title='章节 '+str(i+1),start=i*30,end=(i+1)*30) for i in range(12)],cache_hit=True,elapsed_ms=1)
s=LocalServer(e,ROOT/'web')
(ROOT/'work'/'ui-preview-session.json').write_text(json.dumps({'url':s.url,'pid':__import__('os').getpid()}))
print(s.url,flush=True)
s.serve_forever()
