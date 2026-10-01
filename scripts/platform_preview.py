import json,sys,threading
from pathlib import Path
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'app'))
from core import Engine
from server import LocalServer
e=Engine(ROOT/'work'/'platform-preview',workers=False);e.tools=ROOT/'tools'
s=LocalServer(e,ROOT/'web');(ROOT/'work/platform-preview-session.json').write_text(json.dumps({'url':s.url,'pid':__import__('os').getpid()}));print(s.url,flush=True);s.serve_forever()
