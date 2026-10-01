from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent.parent
image = Image.new('RGBA', (256, 256), '#101823')
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((24, 24, 232, 232), radius=56, fill='#c9f368')
draw.polygon([(104, 72), (104, 184), (184, 128)], fill='#152119')
image.save(root / 'work' / 'clipper.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(256,256)])
