"""Optional network integration test against Google's public player demo video."""
from pathlib import Path
import os
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'app'))
from core import Engine
root=Path(__file__).resolve().parent.parent
os.environ['TEMP']=os.environ['TMP']=str(root/'work')
e=Engine(root/'work'/'live-smoke',workers=False)
e.tools=root/'tools'
info=e.metadata('https://www.youtube.com/watch?v=M7lc1UVf-VE')
result=e.add_jobs(dict(url=info['url'],title=info['title'],quality='360',mode='precise',preset='compatible',clips=[dict(start=3.25,end=7.75,name='live-smoke')]))
job=(result['added'] or [e.jobs()[0]['id']])[0]
e.patch(job,state='downloading',attempt=1)
payload=next(j['payload'] for j in e.jobs() if j['id']==job)
e.execute(job,payload,1)
row=next(j for j in e.jobs() if j['id']==job)
report=dict(title=info['title'],qualities=info['qualities'],state=row['state'],output=row['output'],message=row['message'],detail=row['detail'])
(root/'work'/'live-smoke-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if row['state']=='complete' else 1)
