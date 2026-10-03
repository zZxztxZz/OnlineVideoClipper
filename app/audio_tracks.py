"""Language audio tracks shared by preview, cached cuts and downloads."""
import re

def track_id(value=''):
    if value is None: return ''
    if not isinstance(value,str) or value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}(?::description)?',value):
        raise ValueError('音轨无效，请重新解析视频后选择。')
    return value

def format_track(fmt):
    language=fmt.get('language') or ''
    return language+(':description' if language and re.search('descrip',str(fmt.get('format_note','')),re.I) else '')

def audio_rank(fmt):
    return (fmt.get('language_preference') or 0,fmt.get('preference') or 0,
        int(str(fmt.get('acodec','')).startswith(('mp4a','aac'))),fmt.get('abr') or fmt.get('tbr') or 0)

def audio_tracks(info):
    groups={}
    for fmt in info.get('formats',[]):
        language=fmt.get('language')
        if fmt.get('vcodec')!='none' or fmt.get('acodec')=='none' or not language: continue
        try: track_id(language)
        except ValueError: continue
        groups.setdefault(format_track(fmt),[]).append(fmt)
    result=[]
    for key,formats in groups.items():
        language=key.split(':')[0];description=key.endswith(':description')
        chosen=max(formats,key=audio_rank)
        original=any((f.get('language_preference') or 0)>=10 or 'original' in str(f.get('format_note','')).lower() for f in formats)
        result.append(dict(id=key,language=language,original=original,description=description,
            label=language+(' · 原声' if original else '')+(' · 口述影像' if description else ''),preview=any(f.get('protocol') in ('http','https') and str(f.get('acodec','')).startswith(('mp4a','aac','opus')) for f in formats)))
    return sorted(result,key=lambda x:(x['description'],not x['original'],x['language']))

def audio_selector(track='',m4a=False):
    track=track_id(track)
    language=f'[language={track.split(":")[0]}]' if track else ''
    role='[format_note~="(?i)descrip"]' if track.endswith(':description') else '[format_note!~=?"(?i)descrip"]' if track else ''
    selector=f'bestaudio{language}{role}'
    return f'{selector}[ext=m4a]/{selector}' if m4a else selector

def choose_audio(formats,track=''):
    track=track_id(track)
    candidates=[f for f in formats if f.get('vcodec')=='none' and str(f.get('acodec','')).startswith(('mp4a','aac','opus')) and (not track or format_track(f)==track)]
    if not track and any(not format_track(f).endswith(':description') for f in candidates):
        candidates=[f for f in candidates if not format_track(f).endswith(':description')]
    if not candidates: raise ValueError('此音轨没有可直接预览的 AAC / Opus 音频，请选择其他音轨。')
    return max(candidates,key=audio_rank)
