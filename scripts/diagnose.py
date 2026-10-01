from pathlib import Path
import sys
import subprocess
import os
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'app'))
from core import Engine
root=Path(__file__).resolve().parent.parent
os.environ['TEMP']=os.environ['TMP']=str(root/'work')
e=Engine(root,workers=False)
jobs=e.jobs()
cmd=e.command_for('diagnostic',jobs[0]['payload'],e.settings())
cmd.insert(1,'--verbose')
r=subprocess.run(cmd,capture_output=True,encoding='utf-8',errors='replace')
(root/'work'/'download-diagnostic.log').write_text(e.redact(r.stdout+'\n'+r.stderr),encoding='utf-8')
print('returncode:',r.returncode)
print(e.redact(r.stdout+'\n'+r.stderr))
