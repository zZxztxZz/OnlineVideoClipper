import pathlib
import shutil
import sys
import time
import urllib.request

url, destination = sys.argv[1:]
for attempt in range(4):
    try:
        request = urllib.request.Request(url, headers={'User-Agent':'YouTubeClipper-Setup/0.1'})
        with urllib.request.urlopen(request, timeout=60) as response, open(destination, 'wb') as target:
            shutil.copyfileobj(response, target)
        print(f'{pathlib.Path(destination).name}: {pathlib.Path(destination).stat().st_size} bytes')
        break
    except Exception:
        if attempt == 3:
            raise
        time.sleep(2 ** attempt)
