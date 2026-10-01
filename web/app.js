'use strict';
const $=id=>document.getElementById(id);
let video=null, clips=[], player=null, playerReady=false, selectedEnd=null, jobs=[], filter='all', settings={}, ytPromise=null;
let exactSelection=null;
let toastTimer, previewGeneration=0, saveFull=false, saveItems=[], saveStandalone=false, queuePaused=false, pendingRepair=null, forceParse=false, lastParsedURL="";
let savePreparing=false;
const labels={paused:'已暂停',queued:'等待下载',downloading:'下载中',processing:'合并 / 检查',retrying:'等待重试',complete:'已完成',failed:'失败',cancelled:'已取消'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=n=>{n=Math.max(0,Number(n)||0);const ms=Math.round(n*1000);return [Math.floor(ms/3600000),Math.floor(ms/60000)%60,Math.floor(ms/1000)%60].map(v=>String(v).padStart(2,'0')).join(':')+'.'+String(ms%1000).padStart(3,'0');};
function secs(s){const parts=String(s).trim().split(':');if(parts.length>3||parts.some(v=>!/^\d+(\.\d+)?$/.test(v)))throw Error('时间格式应为 00:01:23.500');const n=parts.reduce((n,v)=>n*60+Number(v),0);if(!Number.isFinite(n)||n<0||parts.slice(1).some(v=>+v>=60))throw Error('时间格式无效');return n;}
function toast(message,error=false){$('toast').textContent=message;$('toast').hidden=false;$('toast').style.background=error?'#ffd0c9':'#d8f99b';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
async function api(path,data){const response=await fetch('/api/'+path,{method:data?'POST':'GET',headers:{'X-Session-Token':window.SESSION_TOKEN,...(data?{'Content-Type':'application/json'}:{})},...(data?{body:JSON.stringify(data)}:{})});const result=await response.json();if(!response.ok){const error=Error(result.error||'请求失败');error.kind=result.kind;throw error;}return result;}
function showView(name){document.querySelectorAll('.view').forEach(e=>e.hidden=e.id!==name);document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.dataset.view===name));}
document.querySelectorAll('.tab').forEach(e=>e.onclick=()=>showView(e.dataset.view));
$('parse-form').onsubmit=async e=>{e.preventDefault();if(clips.length&&!confirm('解析新视频会清空当前尚未加入队列的片段，继续？'))return;$('parse').disabled=true;$('parse-status').classList.remove('error-text');$('parse-status').textContent='正在读取视频信息和可用清晰度…';try{const info=await api('metadata',{url:$('url').value.trim(),force:forceParse});video=info;exactSelection=null;for(const id of ['fine-start','fine-end','capture-frame','download-current'])$(id).disabled=false;$('download-full').disabled=false;clips=[];renderClips();$('video-title').textContent=info.title;$('video-title').title=info.title;$('video-channel').textContent=info.channel;$('video-duration').textContent=time(info.duration).split('.')[0];$('source-link').href=info.url;$('source-link').hidden=false;renderParts(info);lastParsedURL=info.url;$('parse-retry').hidden=$('parse-connect').hidden=true;$('start').value=time(0);$('end').value=time(Math.min(30,info.duration||30));const heights=info.qualities.map(q=>`<option value="${q.height}">${q.height}p${q.fps>=50?' · '+Math.round(q.fps)+'fps':''}</option>`).join('');$('quality').innerHTML='<option value="best">最高可用</option>'+heights+'<option value="audio">仅音频</option>';const available=info.qualities.map(q=>q.height);$('quality').value=available.includes(+settings.quality)?settings.quality:(available.filter(h=>h<=+settings.quality).at(-1)||'best');if(settings.quality==='audio')$('quality').value='audio';$('chapters').innerHTML='';for(const c of info.chapters){const b=document.createElement('button');b.className='quiet';b.textContent=c.title;b.title=`${time(c.start)} → ${time(c.end)}`;b.onclick=()=>{exactSelection=null;$('start').value=time(c.start);$('end').value=time(c.end);$('clip-name').value=c.title;updateDuration();if(playerReady)player.seekTo(c.start,true);};$('chapters').appendChild(b);}$('parse-status').textContent=(info.cache_hit?'已复用解析缓存':`解析完成 · ${(info.elapsed_ms/1000).toFixed(2)} 秒`)+ '。用 I / O 标记片段。';if(pendingRepair){$('start').value=time(pendingRepair.start);$('end').value=time(pendingRepair.end);$('clip-name').value=pendingRepair.name;if([...$('quality').options].some(o=>o.value===pendingRepair.quality))$('quality').value=pendingRepair.quality;$('mode').value=pendingRepair.mode;$('preset').value=pendingRepair.preset;pendingRepair=null;$('parse-status').textContent+=' 已恢复原任务选区，请调整选项后下载。';}updateDuration();preview(info);}catch(ex){video=null;for(const id of ['fine-start','fine-end','capture-frame','download-current'])$(id).disabled=true;$('download-full').disabled=$('enqueue').disabled=true;$('source-options').hidden=true;$('parse-retry').hidden=false;$('parse-connect').hidden=ex.kind!=='auth';$('parse-status').textContent=ex.message;$('parse-status').classList.add('error-text');}finally{$('parse').disabled=false;forceParse=false;}};
function mark(which){exactSelection=null;if(!playerReady)return toast('预览尚未就绪，可以手动输入时间。',true);markDisplayed(which);}
$('mark-start').onclick=()=>mark('start');$('mark-end').onclick=()=>mark('end');
document.addEventListener('keydown',e=>{if(e.ctrlKey||e.metaKey||e.altKey||['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)||$('settings-dialog').open||$('shot-dialog').open||$('save-dialog').open)return;if(e.key.toLowerCase()==='i'){e.preventDefault();mark('start');}if(e.key.toLowerCase()==='o'){e.preventDefault();mark('end');}});
function range(){const start=secs($('start').value),end=secs($('end').value);if(end<=start)throw Error('终点必须晚于起点');if(video?.duration&&end>video.duration+.1)throw Error('终点超出了视频总时长');return {start,end};}
function updateDuration(){try{const r=range();$('selected-duration').textContent=(r.end-r.start).toFixed(3)+' 秒';}catch(e){$('selected-duration').textContent='请检查起止时间';}}
$('start').oninput=$('end').oninput=()=>{exactSelection=null;updateDuration();};
$('preview-range').onclick=async()=>{try{const r=range();if(!video)throw Error('请先解析视频');selectedEnd=r.end;await seekMaterial(r.start,true);}catch(e){toast(e.message,true);}};
setInterval(()=>{if(playerReady){try{const t=player.getCurrentTime();$('current-time').textContent=time(t);if(selectedEnd!==null&&t>=selectedEnd){player.pauseVideo();selectedEnd=null;}}catch(e){}}},150);
$('add-clip').onclick=()=>{try{if(!video)throw Error('请先解析一个视频');const r=currentSelection();if(clips.some(c=>Math.abs(c.start-r.start)<.001&&Math.abs(c.end-r.end)<.001))throw Error('这个片段已在列表中');clips.push({...r,name:$('clip-name').value.trim()||`片段 ${clips.length+1}`});$('clip-name').value='';renderClips();toast('片段已添加，可以继续选取下一段。');}catch(e){toast(e.message,true);}};
function renderClips(){$('clip-count').textContent=clips.length;$('enqueue').disabled=!clips.length;$('clips-list').className=clips.length?'':'empty-list';$('clips-list').innerHTML=clips.length?clips.map((c,i)=>`<div class="clip-row"><span class="number">${String(i+1).padStart(2,'0')}</span><div class="name">${esc(c.name)}${c.shot_cache?' · 逐帧精确':''}<small>${time(c.start)} → ${time(c.end)}</small></div><span class="length">${(c.end-c.start).toFixed(1)} 秒</span><button class="quiet" data-remove="${i}" aria-label="移除片段">✕</button></div>`).join(''):'边看边标记，添加多个片段后一起下载。';}
$('clips-list').onclick=e=>{const b=e.target.closest('[data-remove]');if(b){clips.splice(+b.dataset.remove,1);renderClips();}};$('clear-clips').onclick=()=>{clips=[];renderClips();};
function outputExtension(){return $('quality').value==='audio'?'.m4a':$('preset').value==='compatible'?'.mp4':'.mkv';}
function fileName(value){return String(value).replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').replace(/[. ]+$/g,'').slice(0,110)||'视频';}
function openSave(full,items=null){if(!video||(!full&&!(items||clips).length))return;saveFull=full;saveStandalone=!!items;saveItems=items||clips;const list=full?[{name:'完整视频'}]:saveItems;const ext=outputExtension();$('save-files').innerHTML=list.map((c,i)=>{const name=fileName(video.title+(full?'':'__'+c.name+'__'+i));const path=(settings.last_output_dir||settings.output_dir||'').replace(/[\\/]+$/,'')+'\\'+name+ext;return `<label>${full?'完整视频':esc(c.name)}<input data-save-path="${i}" value="${esc(path)}" required></label><button class="secondary" type="button" data-save-browse="${i}">另存为…</button>`;}).join('');$('save-dialog').showModal();}
$('enqueue').onclick=()=>openSave(false);
$('download-full').onclick=()=>openSave(true);
$('save-close').onclick=()=>$('save-dialog').close();
$('save-files').onclick=async e=>{const b=e.target.closest('[data-save-browse]');if(!b)return;const input=$('save-files').querySelector(`[data-save-path="${b.dataset.saveBrowse}"]`);b.disabled=true;try{const r=await api('choose-save-file',{initial:input.value.trim()});if(!r.cancelled)input.value=r.path;}catch(e){toast(e.message,true);}finally{b.disabled=false;}};
$('save-form').onsubmit=async e=>{
  e.preventDefault();if(!video||(!saveFull&&!saveItems.length))return;$('download-confirm').disabled=true;savePreparing=true;$('save-prepare-status').textContent='';
  try{
    const options={quality:$('quality').value,mode:$('mode').value,preset:$('preset').value};
    const paths=[...$('save-files').querySelectorAll('[data-save-path]')].map(e=>e.value.trim());
    if(!saveFull&&$('quality').value!=='audio'){for(const c of saveItems){if(!c.shot_cache&&c.end-c.start<=600){const segments=await ensureExport(c,()=>!$('save-dialog').open);c.player_segments=segments;}}}
    if(!$('save-dialog').open)throw Error('已取消准备选区');
    const selection=saveFull?{full:true,duration:video.duration,output_path:paths[0]}:{clips:saveItems.map((c,i)=>({...c,output_path:paths[i]}))};
    const r=await api('jobs',{url:video.url,title:video.title,...options,...selection});
    toast(`已加入 ${r.added.length} 个任务${r.duplicates?'，跳过 '+r.duplicates+' 个重复任务':''}`);
    savePreparing=false;$('save-dialog').close();if(!saveFull&&!saveStandalone){clips=[];renderClips();}showView('queue');await refresh();
  }catch(e){toast(e.message,true);}finally{savePreparing=false;$('download-confirm').disabled=false;$('save-prepare-status').textContent='';}
};
$('save-dialog').addEventListener('close',()=>{if(savePreparing&&material)seekMaterial(materialTime,false);});
document.querySelectorAll('.filter').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('.filter').forEach(e=>e.classList.toggle('active',e===b));renderJobs();});
let lastJobsHTML='';
function bytes(n){if(!Number.isFinite(Number(n)))return '—';n=Number(n);const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++;}return n.toFixed(i?1:0)+' '+units[i];}
function metricsText(j){const m=j.metrics||{};const items=[];if(m.downloaded_bytes!=null)items.push(bytes(m.downloaded_bytes)+(m.total_bytes||m.total_bytes_estimate?' / '+bytes(m.total_bytes||m.total_bytes_estimate):''));if(m.processed_bytes!=null)items.push('已生成 '+bytes(m.processed_bytes));if(m.speed>0&&j.state==='downloading')items.push((m.estimated?'处理约 ':'')+bytes(m.speed)+'/s');if(m.eta!=null&&j.state==='downloading')items.push((m.estimated?'预计 ':'剩余 ')+time(m.eta).split('.')[0]);return items.join(' · ');}
function jobButton(j,action,text){return `<button class="secondary" data-id="${j.id}" data-action="${action}">${text}</button>`;}
function renderJobs(){
 const active=['queued','downloading','processing','retrying'];const count=jobs.filter(j=>active.includes(j.state)).length;
 $('queue-count').textContent=count;$('queue-summary').textContent=count+' 个待完成 · '+jobs.filter(j=>j.state==='paused').length+' 个暂停';
 const selected=jobs.filter(j=>filter==='all'||(filter==='active'?[...active,'paused'].includes(j.state):filter==='failed'?['failed','cancelled'].includes(j.state):j.state===filter));
 const html=selected.length?selected.map(j=>{const p=j.payload;let actions='';
  if(j.state==='complete')actions+=jobButton(j,'open','播放')+jobButton(j,'folder','打开目录');
  if(active.includes(j.state))actions+=jobButton(j,'pause','暂停')+jobButton(j,'cancel','取消');
  if(j.state==='paused')actions+=jobButton(j,'resume',p.full?'继续下载':'重新下载片段')+jobButton(j,'cancel','取消');
  if(['queued','retrying','paused'].includes(j.state))actions+=jobButton(j,'up','↑')+jobButton(j,'down','↓')+jobButton(j,'first','优先下载');
  if(['failed','cancelled','retrying'].includes(j.state))actions+=jobButton(j,'retry','立即重试');
  if(j.state==='failed'){
   if(j.failure_kind==='auth')actions+=jobButton(j,'connect',p.platform==='youtube'?'配置 Cookie':'重新连接平台');
   if(j.failure_kind==='storage')actions+=jobButton(j,'relocate','更改保存位置');
   actions+=jobButton(j,'repair','重新解析 / 修改选项');
  }
  const cause={auth:'登录 / 验证',format:'清晰度 / 格式',storage:'文件写入',unavailable:'视频不可用',network:'网络连接',processing:'媒体处理'}[j.failure_kind];
  return `<article class="job card"><div class="job-top"><div><h3>${esc(p.name)} <span class="muted">/ ${esc(p.title)}</span></h3><p class="sub">${p.full?'完整视频':time(p.start)+' → '+time(p.end)} · ${p.quality==='audio'?'仅音频':p.quality==='best'?'最高可用':esc(p.quality)+'p'} · ${p.full?'自动合并':p.mode==='precise'?'精确切割':'快速切割'} · 尝试 ${j.attempt} 次</p></div><span class="badge ${esc(j.state)}">${labels[j.state]||esc(j.state)}</span></div><div class="progress"><span style="width:${Math.min(100,Math.max(0,j.progress))}%"></span></div><p class="transfer">${esc(metricsText(j))}</p><div class="job-bottom"><p>${cause&&j.state==='failed'?esc(cause)+'：':''}${esc(j.message)}</p><div class="job-actions">${actions}</div></div>${j.detail?`<details><summary>查看失败详情</summary><pre>${esc(j.detail)}</pre></details>`:''}${j.output?`<p class="hint">${esc(j.output)}</p>`:''}</article>`;
 }).join(''):'<div class="card empty-list">这里还没有任务。解析视频后即可下载。</div>';
 if(html!==lastJobsHTML){const scroll=$('jobs-list').scrollTop;const opened=[...$('jobs-list').querySelectorAll('details[open]')].map(d=>d.parentElement.querySelector('[data-id]')?.dataset.id);$('jobs-list').innerHTML=html;$('jobs-list').scrollTop=scroll;$('jobs-list').querySelectorAll('details').forEach(d=>{if(opened.includes(d.parentElement.querySelector('[data-id]')?.dataset.id))d.open=true;});lastJobsHTML=html;}
}
let playingPath='', playingId='';
function closeMedia(){$('local-video').pause();$('local-video').removeAttribute('src');$('local-video').load();}
$('media-close').onclick=()=>$('media-dialog').close();$('media-dialog').addEventListener('close',closeMedia);
$('local-video').onerror=()=>$('media-error').hidden=false;
$('media-copy').onclick=async()=>{try{await navigator.clipboard.writeText(playingPath);toast('文件路径已复制');}catch(e){toast(playingPath,true);}};
$('jobs-list').onclick=async e=>{const b=e.target.closest('[data-action]');if(!b)return;b.disabled=true;try{if(b.dataset.action==='repair'){const job=jobs.find(j=>j.id===b.dataset.id);pendingRepair={...job.payload};$('url').value=job.payload.url;forceParse=true;showView('studio');$('parse-form').requestSubmit();return;}if(b.dataset.action==='connect'){const job=jobs.find(j=>j.id===b.dataset.id);$('settings-open').click();const site=job.payload.platform;if(site==='youtube'){$('setting-cookies').focus();}else{$('connect-'+site).click();}return;}if(b.dataset.action==='relocate'){const job=jobs.find(j=>j.id===b.dataset.id),p=job.payload;const ext=p.quality==='audio'?'.m4a':p.preset==='compatible'?'.mp4':'.mkv';const r=await api('choose-save-file',{initial:p.output_dir+'\\'+(p.filename||fileName(p.title)+ext)});if(!r.cancelled){await api('action',{id:job.id,action:'relocate',path:r.path});toast('保存位置已更改，点击重试继续。');await refresh();}return;}if(b.dataset.action==='open'){const job=jobs.find(j=>j.id===b.dataset.id);if(!job)throw Error('任务不存在');await api('action',{id:job.id,action:'open'});playingPath=job.output;playingId=job.id;$('media-title').textContent=job.payload.name;$('media-error').hidden=true;$('local-video').src=`media/${job.id}`;$('media-dialog').showModal();$('local-video').play().catch(()=>{});}else{const r=await api('action',{id:b.dataset.id,action:b.dataset.action});if(r.message)toast(r.message);await refresh();}}catch(e){toast(e.message,true);}finally{b.disabled=false;}};
async function refresh(){try{const r=await api('state');jobs=r.jobs;settings=r.settings;queuePaused=settings.queue_paused;$('queue-toggle').textContent=queuePaused?'恢复启动新任务':'暂停启动新任务';for(const site of ['douyin','bilibili']){$('finish-'+site).hidden=!r.connections?.[site];$('connect-'+site).disabled=$('import-'+site).disabled=!r.desktop;$('connect-'+site).textContent=r.connections?.[site]?'显示连接窗口':(settings[site+'_cookies']?'重新连接':'连接')+(site==='douyin'?'抖音':'B站');}$('destination').textContent='保存位置：'+(settings.last_output_dir||settings.output_dir);$('destination').title=settings.last_output_dir||settings.output_dir;$('connection').textContent='后台在线';$('connection').classList.remove('offline');$('tools-status').innerHTML=Object.entries(r.status.tools).map(([k,v])=>`<span>${v?'✓':'✕'} ${esc(k)}</span>`).join('');renderJobs();return r;}catch(e){$('connection').textContent='后台已停止';$('connection').classList.add('offline');}}
$('settings-open').onclick=()=>{$('setting-output').value=settings.output_dir||'';$('setting-concurrency').value=settings.concurrency||2;$('setting-retries').value=settings.retries??3;$('setting-proxy').value=settings.proxy||'';$('setting-cookies').value=settings.cookies||'';for(const site of ['bilibili','douyin']){ $('setting-'+site+'-cookies').value=settings[site+'_cookies']||'';$('setting-'+site+'-proxy').value=settings[site+'_proxy']||'';}$('settings-dialog').showModal();};$('settings-close').onclick=()=>$('settings-dialog').close();
$('settings-form').onsubmit=async e=>{e.preventDefault();try{await api('settings',{output_dir:$('setting-output').value.trim(),concurrency:+$('setting-concurrency').value,retries:+$('setting-retries').value,proxy:$('setting-proxy').value.trim(),cookies:$('setting-cookies').value.trim(),bilibili_cookies:$('setting-bilibili-cookies').value.trim(),douyin_cookies:$('setting-douyin-cookies').value.trim(),bilibili_proxy:$('setting-bilibili-proxy').value.trim(),douyin_proxy:$('setting-douyin-proxy').value.trim()});$('settings-dialog').close();await refresh();toast('设置已保存。新任务使用新的保存目录。');}catch(ex){toast(ex.message,true);}};
$('update-engine').onclick=async()=>{$('update-engine').disabled=true;$('update-status').textContent='正在检查并更新下载引擎…';try{const r=await api('update',{});$('update-status').textContent=r.message;}catch(e){$('update-status').textContent=e.message;}finally{$('update-engine').disabled=false;}};
for(const site of ['douyin','bilibili']){
  $('connect-'+site).onclick=async()=>{try{await api('connect/start',{site});$('connect-status-'+site).textContent='在新窗口浏览或登录，然后返回这里点击“完成连接”。';await refresh();}catch(e){toast(e.message,true);}};
  $('finish-'+site).onclick=async()=>{const b=$('finish-'+site);b.disabled=true;try{const r=await api('connect/finish',{site});$('setting-'+site+'-cookies').value=r.path;$('connect-status-'+site).textContent='Cookie 已自动保存。请重新解析视频；此状态不代表已经验证下载成功。';toast(r.message);await refresh();}catch(e){toast(e.message,true);}finally{b.disabled=false;}};
  $('import-'+site).onclick=async()=>{try{const r=await api('choose-cookie',{});if(!r.cancelled){$('setting-'+site+'-cookies').value=r.path;await api('settings',{[site+'_cookies']:r.path});await refresh();$('connect-status-'+site).textContent='Cookie 文件已导入，请重新解析视频。';}}catch(e){toast(e.message,true);}};
}
refresh().then(()=>{$('mode').value=settings.mode||'fast';$('preset').value=settings.preset||'compatible';$('preview-volume').value=settings.volume??100;$('remote-video').muted=!!settings.muted;setPreviewVolume();});setInterval(refresh,2000);

$('media-system').onclick=async()=>{const b=$('media-system');b.disabled=true;try{const r=await api('action',{id:playingId,action:'system-play'});toast(r.message);}catch(e){toast(e.message,true);}finally{b.disabled=false;}};

function stopRemote(){const v=$('remote-video'),a=$('remote-audio');v.pause();a.pause();for(const e of [v,a]){e.removeAttribute('src');e.load();}v.hidden=true;$('volume-controls').hidden=true;}
function renderParts(info){const parts=info.parts||[];$('source-options').hidden=parts.length<2&&!info.parts_error;$('parts-retry').hidden=!info.parts_error;$('parts-retry').title=info.parts_error||'';$('part-list').innerHTML=parts.map(p=>`<button type="button" class="quiet ${p.page===info.part?'selected':''}" data-part="${p.page}" title="${esc(p.title)}">${esc(p.title)}</button>`).join('');}
$('part-list').onclick=e=>{const b=e.target.closest('[data-part]');if(!b||!video)return;const u=new URL(video.url);u.searchParams.set('p',b.dataset.part);$('url').value=u.href;$('parse-form').requestSubmit();};
$('parts-retry').onclick=async()=>{if(!video)return;try{const r=await api('parts',{url:video.url});video.parts=r.parts;video.parts_error=r.error;renderParts(video);if(r.error)toast(r.error,true);}catch(e){toast(e.message,true);}};


function setPreviewVolume(){const v=$('remote-video'),a=$('remote-audio');v.volume=Number($('preview-volume').value)/100;a.volume=v.volume;a.muted=v.muted;$('preview-mute').textContent=v.muted?'取消静音':'静音';}
$('preview-volume').oninput=()=>{const v=$('remote-video');v.muted=false;setPreviewVolume();$('preview-mute').textContent='静音';};
$('preview-mute').onclick=()=>{const v=$('remote-video');v.muted=!v.muted;$('remote-audio').muted=v.muted;$('preview-mute').textContent=v.muted?'取消静音':'静音';};

$('queue-toggle').onclick=async()=>{try{await api('settings',{queue_paused:!queuePaused});await refresh();toast(queuePaused?'已暂停启动新任务，当前下载继续。':'已恢复启动等待任务。');}catch(e){toast(e.message,true);}};
let volumeSaveTimer;
function rememberVolume(){clearTimeout(volumeSaveTimer);volumeSaveTimer=setTimeout(()=>{const volume=Number($('preview-volume').value),muted=$('remote-video').muted;settings.volume=volume;settings.muted=muted;api('settings',{volume,muted}).catch(e=>toast(e.message,true));},250);}
$('preview-volume').addEventListener('input',rememberVolume);$('preview-mute').addEventListener('click',rememberVolume);
$('quality').onchange=$('mode').onchange=$('preset').onchange=event=>{if(event.target.id==='quality'&&video)preview(video,true);if(event.target.id==='mode'&&$('mode').value!=='precise')exactSelection=null;if(exactSelection&&exactSelection.quality!==$('quality').value)exactSelection=null;api('settings',{quality:$('quality').value,mode:$('mode').value,preset:$('preset').value}).then(s=>Object.assign(settings,s)).catch(e=>toast(e.message,true));};
$('parse-retry').onclick=()=>{forceParse=true;$('parse-form').requestSubmit();};
$('parse-connect').onclick=()=>{$('settings-open').click();const url=$('url').value;if(/douyin/.test(url))$('connect-douyin').click();else if(/bilibili|b23\.tv/.test(url))$('connect-bilibili').click();else $('setting-cookies').focus();};

let ytVolumeTimer;
setInterval(()=>{if(!playerReady||!player?.getVolume)return;try{const volume=Math.round(player.getVolume()),muted=player.isMuted();if(volume!==settings.volume||muted!==settings.muted){settings.volume=volume;settings.muted=muted;clearTimeout(ytVolumeTimer);ytVolumeTimer=setTimeout(()=>api('settings',{volume,muted}).catch(e=>toast(e.message,true)),350);}}catch(e){}},500);
let shot=null, shotId=null, shotGeneration=0, shotIndex=0, shotStart=0, shotEnd=0, shotFrameGeneration=0, shotTimer, shotPlayEnd=null;
const shotTime=i=>i<shot.times.length?shot.times[i]:shot.last_end;
function shotControls(ready){$('shot-ready').hidden=!ready;$('shot-analyze').disabled=!!shotId&&!shot;$('shot-center').disabled=!!shotId&&!shot;}
async function analyzeShot(radius=12){
 const generation=++shotGeneration;clearTimeout(shotTimer);shot=null;shotId=null;shotControls(false);$('shot-expand').hidden=true;$('shot-status').textContent='正在读取附近视频…';
 try{const center=secs($('shot-center').value);const result=await api('scene/start',{url:video.url,center,quality:$('quality').value,duration:video.duration,radius});
 if(generation!==shotGeneration){await api('scene/cancel',{id:result.id});return;}shotId=result.id;shotControls(false);
 const poll=async()=>{try{const state=await api('scene/status',{id:result.id});if(generation!==shotGeneration)return;$('shot-status').textContent=state.message;
 if(state.state==='working'){shotTimer=setTimeout(poll,800);return;}if(state.state!=='ready')throw Error(state.message);
 shot=state;shotIndex=state.selected_shot;shotControls(true);$('shot-video').src=`shots/${shotId}/preview.mp4`;$('shot-video').volume=(settings.volume??100)/100;$('shot-video').muted=!!settings.muted;selectShot(shotIndex);
 }catch(e){if(generation!==shotGeneration)return;shotId=null;shotControls(false);$('shot-status').textContent=e.message;}};await poll();
 }catch(e){if(generation!==shotGeneration)return;shotId=null;shotControls(false);$('shot-status').textContent=e.message;}
}
$('select-shot').onclick=()=>{if(!video)return toast('请先解析视频。',true);if($('quality').value==='audio')return toast('请先选择视频清晰度。',true);let center=0;try{center=playerReady?player.getCurrentTime():secs($('start').value);}catch(e){}if(playerReady)player.pauseVideo();$('shot-center').value=time(Math.max(0,Math.min(center,video.duration-.001)));$('shot-dialog').showModal();if(shot&&shot.url===video.url&&shot.quality===$('quality').value&&center>=shot.times[0]&&center<shot.last_end){shotControls(true);$('shot-video').src=`shots/${shotId}/preview.mp4`;selectShot(shot.shots.findIndex(s=>shotTime(s.start_index)<=center&&center<shotTime(s.end_index)));}else analyzeShot();};
$('shot-analyze').onclick=()=>analyzeShot();$('shot-expand').onclick=()=>analyzeShot(60);
$('shot-close').onclick=()=>$('shot-dialog').close();$('shot-dialog').addEventListener('close',()=>{++shotGeneration;++shotFrameGeneration;clearTimeout(shotTimer);if(shotId&&!shot)api('scene/cancel',{id:shotId}).catch(()=>{});$('shot-video').pause();$('shot-video').removeAttribute('src');$('shot-video').load();shotPlayEnd=null;});
function selectShot(index){shotIndex=index;const selection=shot.shots[index];shotStart=selection.start_index;shotEnd=selection.end_index;$('shot-prev').disabled=index===0;$('shot-next').disabled=index===shot.shots.length-1;
 const unresolved=!selection.left_found||!selection.right_found;$('shot-warning').hidden=!unresolved;$('shot-warning').textContent='这个镜头的'+(!selection.left_found&&!selection.right_found?'两侧':!selection.left_found?'起点':'终点')+'超出已读取范围。可扩大搜索，或以当前边界导出局部片段。';$('shot-expand').hidden=!unresolved||shot.radius>=60;updateShotFrames();}
async function updateShotFrames(){const generation=++shotFrameGeneration;const a=shotStart,b=shotEnd;$('shot-start-time').textContent=time(shotTime(a));$('shot-end-time').textContent=time(shotTime(b));$('shot-summary').textContent=`镜头 ${shotIndex+1} / ${shot.shots.length} · ${b-a} 帧 · ${(shotTime(b)-shotTime(a)).toFixed(3)} 秒`;
 for(const button of document.querySelectorAll('[data-step]')){const i=button.dataset.step==='start'?a:b,d=+button.dataset.delta;button.disabled=button.dataset.step==='start'?i+d<0||i+d>=b:i+d<=a||i+d>shot.times.length;}
 $('shot-add').disabled=$('shot-download').disabled=true;
 try{const frames=await api('scene/frames',{id:shotId,start_index:a,end_index:b});if(generation!==shotFrameGeneration)return;for(const name of ['start_before','start_after','end_before','end_after']){const img=$('shot-'+name.replace('_','-'));if(frames[name]){img.src=frames[name];img.style.visibility='visible';}else{img.removeAttribute('src');img.style.visibility='hidden';}}$('shot-add').disabled=$('shot-download').disabled=false;$('shot-status').textContent='边界帧已就绪。可逐帧微调，或直接下载。';
 }catch(e){if(generation===shotFrameGeneration)$('shot-status').textContent=e.message;}}
for(const button of document.querySelectorAll('[data-step]'))button.onclick=()=>{if(button.dataset.step==='start')shotStart+=+button.dataset.delta;else shotEnd+=+button.dataset.delta;shotPlayEnd=null;$('shot-video').pause();$('shot-video').currentTime=shotTime(button.dataset.step==='start'?shotStart:shotEnd-1)-shot.times[0];updateShotFrames();};
$('shot-prev').onclick=()=>selectShot(shotIndex-1);$('shot-next').onclick=()=>selectShot(shotIndex+1);
$('shot-play').onclick=()=>{const v=$('shot-video');v.currentTime=shotTime(shotStart)-shot.times[0];shotPlayEnd=shotTime(shotEnd)-shot.times[0];v.play().catch(()=>{});};$('shot-video').ontimeupdate=()=>{const v=$('shot-video');if(shotPlayEnd!==null&&v.currentTime>=shotPlayEnd){v.pause();shotPlayEnd=null;}};
function shotClip(){if(!shot||shot.quality!==$('quality').value||shot.url!==video.url)throw Error('视频或清晰度已改变，请重新识别。');return {start:shotTime(shotStart),end:shotTime(shotEnd),name:$('clip-name').value.trim()||`镜头 ${shotIndex+1}`,shot_cache:shotId,shot_start_frame:shotStart,shot_end_frame:shotEnd};}
$('shot-add').onclick=()=>{try{const c=shotClip();if(clips.some(x=>x.shot_cache===c.shot_cache&&x.shot_start_frame===c.shot_start_frame&&x.shot_end_frame===c.shot_end_frame))throw Error('这个镜头已在列表中。');clips.push(c);renderClips();$('shot-dialog').close();toast('镜头已添加，将按帧精确导出。');}catch(e){toast(e.message,true);}};
$('shot-download').onclick=()=>{try{const c=shotClip();$('mode').value='precise';$('shot-dialog').close();openSave(false,[c]);}catch(e){toast(e.message,true);}};

function currentSelection(){const r=range();if(exactSelection&&exactSelection.url===video?.url&&exactSelection.quality===$('quality').value&&Math.abs(exactSelection.start-r.start)<.001&&Math.abs(exactSelection.end-r.end)<.001)return {...r,shot_cache:exactSelection.shot_cache,shot_start_frame:exactSelection.shot_start_frame,shot_end_frame:exactSelection.shot_end_frame};return {...r,player_pins:Object.values(materialMarks).map(m=>m.id)};}
$('download-current').onclick=()=>{try{if(!video)throw Error('请先解析视频。');openSave(false,[{...currentSelection(),name:$('clip-name').value.trim()||'当前选区'}]);}catch(e){toast(e.message,true);}};

// One source-backed player. Absolute source timestamps never come from an iframe.
let material=null,materialIndex=0,materialTime=0,materialRole='cursor',materialIntent=false;
let materialGeneration=0,materialImageGeneration=0,materialLoading=false,materialRanges=[],materialExporting=false;
let materialFrameSeen=null,materialSeekTimer,materialStepTimer,materialPrefetchAt=0;
let materialNavigation=0;
let materialMarks={};
let materialScrubbing=false,materialScrubResume=false;
$('buffer-status').title='蓝色：已加载；绿色：下载选区。开头目标 8 秒；跳转优先前 3 秒、后 12 秒；播放向前预取 24 秒。关键帧和解码上下文可能扩大实际范围。浏览缓冲目标上限 120 秒 / 512 MiB，下载选区可临时扩展至 10 分钟；正在使用的素材受到保护。';
const materialVideo=$('remote-video');
const materialQuality=()=>$('quality').value==='audio'?'best':$('quality').value;
const sleepUI=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function materialPins(){return [...new Set(clips.flatMap(c=>(c.player_segments?.map(s=>s.id)||[c.shot_cache]).concat(c.player_pins||[])).concat(Object.values(materialMarks).map(m=>m.id),exactSelection?.shot_cache||[],material?.id||[]).filter(Boolean))];}
function materialFloor(record,t){let a=0,b=record.times.length;while(a<b){const m=(a+b)>>1;if(record.times[m]<=t+.0004)a=m+1;else b=m;}return Math.max(0,Math.min(record.times.length-1,a-1));}
function materialEnd(record,i){return i+1<record.times.length?record.times[i+1]:record.last_end;}
function materialButtons(ready){for(const id of ['material-play','material-back','material-forward','capture-frame'])$(id).disabled=!ready;$('material-seek').disabled=!video;}
function drawMaterialRanges(){
 const duration=video?.duration||1;
 $('buffer-track').innerHTML=materialRanges.map(r=>`<span style="left:${100*r.start/duration}%;width:${100*(r.end-r.start)/duration}%"></span>`).join('');
 try{const r=range();$('selection-track').style.left=(100*r.start/duration)+'%';$('selection-track').style.width=(100*(r.end-r.start)/duration)+'%';}catch(e){}
}
function updateMaterialPosition(){if(!material)return;materialTime=material.record.times[materialIndex];if(!materialScrubbing){$('current-time').textContent=time(materialTime);$('material-seek').value=materialTime;}$('material-role').textContent=(materialRole==='start'?'起点 · 第一帧保留':materialRole==='end'?'终点 · 最后一帧保留':'当前画面')+' · '+time(materialTime);}
async function preview(info,preserve=false){
 const t=preserve?materialTime:0;materialGeneration++;materialImageGeneration++;material=null;materialRanges=[];materialMarks={};materialRole='cursor';materialIntent=false;materialFrameSeen=null;
 materialVideo.onpause=null;materialVideo.pause();stopRemote();$('cursor-image').hidden=true;$('empty-player').hidden=true;$('volume-controls').hidden=false;materialVideo.hidden=false;
 $('material-seek').max=info.duration;materialTime=Math.min(t,info.duration-.001);$('material-seek').value=materialTime;drawMaterialRanges();
 player={getCurrentTime:()=>materialTime,seekTo:t=>seekMaterial(t,materialIntent),playVideo:()=>playMaterial(),pauseVideo:()=>pauseMaterial(),destroy:()=>{materialIntent=false;materialVideo.pause();}};
 await seekMaterial(materialTime,false,!preserve);
}
async function loadMaterial(t,first=false){
 const generation=++materialGeneration;materialLoading=true;playerReady=false;materialButtons(false);$('material-retry').hidden=true;$('player-error').hidden=true;
 const state=await api('player/request',{url:video.url,quality:materialQuality(),duration:video.duration,time:t,first,keep:materialPins()});
 let result=state;const serial=state.serial;
 while(generation===materialGeneration&&result.state==='working'){
  $('buffer-status').textContent=result.message;await sleepUI(250);if(generation!==materialGeneration)return null;
  result=await api('player/status',{});if(result.serial!==serial)return null;
 }
 if(generation!==materialGeneration)return null;
 materialRanges=result.ranges;drawMaterialRanges();
 if(result.state!=='ready')throw Error(result.message);
 materialLoading=false;playerReady=true;materialButtons(true);$('buffer-status').textContent='已缓冲当前位置 · 播放时自动向前加载';
 return {id:result.id,record:result.record};
}
async function seekMaterial(t,play=false,first=false){
 if(!video)return;const navigation=++materialNavigation,target=Math.max(0,Math.min(Number(t)||0,video.duration-.001));materialIntent=play;materialTime=target;
 materialVideo.onpause=null;materialVideo.pause();materialFrameSeen=null;materialImageGeneration++;$('cursor-image').hidden=true;$('material-seek').value=target;
 try{
  const loaded=await loadMaterial(target,first);if(!loaded||navigation!==materialNavigation)return;
  const changed=material?.id!==loaded.id;material=loaded;
  if(changed){await new Promise((resolve,reject)=>{materialVideo.onloadedmetadata=resolve;materialVideo.onerror=()=>reject(Error('本地预览加载失败，请重试。'));materialVideo.src=`shots/${loaded.id}/preview.mp4`;});}
  if(navigation!==materialNavigation)return;
  materialIndex=materialFloor(material.record,target);updateMaterialPosition();materialVideo.currentTime=materialTime-material.record.times[0];materialVideo.onpause=onMaterialPause;
  if(play)await playMaterial();else await showMaterialFrame();
 }catch(e){if(navigation!==materialNavigation)return;materialLoading=false;materialIntent=false;playerReady=false;materialButtons(false);$('buffer-status').textContent=e.message;$('material-retry').hidden=false;}
}
async function showMaterialFrame(){
 if(!material||materialLoading)return;const generation=++materialImageGeneration,id=material.id,index=materialIndex;
 materialButtons(false);
 try{const frame=await api('scene/frame',{id,index});if(generation!==materialImageGeneration)return;
  const img=$('cursor-image');img.src=frame.image;await img.decode();if(generation!==materialImageGeneration)return;
  img.hidden=false;materialVideo.hidden=false;materialButtons(true);updateMaterialPosition();
  $('buffer-status').textContent=`当前画面 ${frame.width} × ${frame.height} · ← / → 逐帧 · I 起点 / O 终点`;
 }catch(e){if(generation===materialImageGeneration){$('buffer-status').textContent=e.message;materialButtons(true);}}
}
async function playMaterial(){
 materialRole='cursor';materialIntent=true;if(materialLoading)return;if(!material)return;
 ++materialImageGeneration;$('cursor-image').hidden=true;materialButtons(true);$('material-play').textContent='Ⅱ';
 materialVideo.volume=Number($('preview-volume').value)/100;materialVideo.muted=!!settings.muted;
 try{await materialVideo.play();}catch(e){materialIntent=false;$('material-play').textContent='▶';$('buffer-status').textContent='播放未开始，请点击播放重试。';}
}
function pauseMaterial(){materialIntent=false;materialVideo.pause();if(materialVideo.paused)onMaterialPause();}
function onMaterialPause(){
 if(materialLoading||!material||materialIntent)return;$('material-play').textContent='▶';
 const t=materialFrameSeen??(material.record.times[0]+materialVideo.currentTime);materialIndex=materialFloor(material.record,t);updateMaterialPosition();showMaterialFrame();
}
function observeMaterialFrame(){
 if(!materialVideo.requestVideoFrameCallback)return;
 materialVideo.requestVideoFrameCallback((_,metadata)=>{
  if(material&&!materialLoading&&!materialVideo.paused){materialFrameSeen=material.record.times[0]+metadata.mediaTime;materialIndex=materialFloor(material.record,materialFrameSeen);updateMaterialPosition();}
  observeMaterialFrame();
 });
}
observeMaterialFrame();
materialVideo.ontimeupdate=()=>{if(material&&!materialLoading&&!materialVideo.paused&&!materialVideo.requestVideoFrameCallback){materialIndex=materialFloor(material.record,material.record.times[0]+materialVideo.currentTime);updateMaterialPosition();}};
materialVideo.onended=()=>{if(!material||!materialIntent)return;const next=material.record.last_end;if(next<video.duration-.001)seekMaterial(next,true);else{materialIntent=false;onMaterialPause();}};
materialVideo.onpause=onMaterialPause;
async function stepMaterial(delta){
 if(!material||materialLoading)return;materialIntent=false;materialVideo.onpause=null;materialVideo.pause();++materialImageGeneration;
 const wanted=materialIndex+delta;
 if(wanted<0||wanted>=material.record.times.length){
  const target=wanted<0?Math.max(0,material.record.times[0]-.001):material.record.last_end;
  await seekMaterial(target,false);if(wanted<0&&material)materialIndex=Math.max(0,materialIndex-(Math.abs(delta)-1));
 }else materialIndex=wanted;
 if(!material)return;materialFrameSeen=null;updateMaterialPosition();materialVideo.currentTime=materialTime-material.record.times[0];materialVideo.onpause=onMaterialPause;
 if(materialRole==='start'||materialRole==='end')markDisplayed(materialRole);
 clearTimeout(materialStepTimer);materialStepTimer=setTimeout(showMaterialFrame,35);
}
function markDisplayed(which){
 if(!material||materialLoading)return;exactSelection=null;
 materialMarks[which]={id:material.id,index:materialIndex};$('mode').value='precise';
 $(which).value=time(which==='end'?materialEnd(material.record,materialIndex):material.record.times[materialIndex]);updateDuration();drawMaterialRanges();
}
$('material-play').onclick=()=>materialIntent?pauseMaterial():playMaterial();
$('material-back').onclick=()=>stepMaterial(-1);$('material-forward').onclick=()=>stepMaterial(1);
$('material-seek').oninput=()=>{clearTimeout(materialSeekTimer);$('current-time').textContent=time($('material-seek').value);};
$('material-seek').onpointerdown=()=>{materialScrubResume=materialIntent;materialScrubbing=true;pauseMaterial();};
$('material-seek').onchange=()=>{const resume=materialScrubbing?materialScrubResume:materialIntent;materialScrubbing=false;materialRole='cursor';return seekMaterial(+$('material-seek').value,resume);};
$('material-seek').onpointerup=()=>{setTimeout(()=>{if(materialScrubbing){materialScrubbing=false;if(materialScrubResume)playMaterial();}},20);};
$('material-retry').onclick=()=>seekMaterial(materialTime,false);
$('fine-start').onclick=async()=>{materialRole='start';await seekMaterial(secs($('start').value),false);};
$('fine-end').onclick=async()=>{materialRole='end';await seekMaterial(Math.max(0,secs($('end').value)-.001),false);};
$('start').addEventListener('input',()=>{delete materialMarks.start;drawMaterialRanges();});$('end').addEventListener('input',()=>{delete materialMarks.end;drawMaterialRanges();});
document.addEventListener('keydown',e=>{
 if(e.ctrlKey||e.metaKey||e.altKey||['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)||document.querySelector('dialog[open]'))return;
 if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();stepMaterial((e.key==='ArrowLeft'?-1:1)*(e.shiftKey?5:1));}
 if(e.code==='Space'){e.preventDefault();$('material-play').click();}
});
$('capture-frame').onclick=async()=>{
 if(!material||materialLoading)return;pauseMaterial();await showMaterialFrame();const id=material.id,index=materialIndex,stamp=material.record.times[index];
 try{const ext=$('snapshot-format').value,initial=(settings.last_output_dir||settings.output_dir||'').replace(/[\\/]+$/,'')+'\\'+fileName(video.title)+'__'+time(stamp).replace(/:/g,'-')+'.'+ext;
  const chosen=await api('choose-save-file',{initial});if(chosen.cancelled)return;const saved=await api('scene/snapshot',{id,index,path:chosen.path});settings.last_output_dir=saved.path.replace(/[\\/][^\\/]+$/,'');toast(`截图已保存：${saved.path}`);
 }catch(e){toast(e.message,true);}
};
// Prefetch only while playing, with one outstanding request and a cooldown after failures.
setInterval(async()=>{
 if(!video||materialLoading||materialExporting)return;
 try{const state=await api('player/status',{});materialRanges=state.ranges;drawMaterialRanges();
  if(!material||!materialIntent||state.prefetch||Date.now()<materialPrefetchAt)return;
  const next=material.record.last_end;
  const contiguous=materialRanges.filter(r=>r.start<=next+.0005&&r.end>next+.0005).reduce((end,r)=>Math.max(end,r.end),next);
  if(contiguous-materialTime<(state.prefetch_lead||10)&&contiguous<video.duration-.001){materialPrefetchAt=Date.now()+5000;
   await api('player/request',{url:video.url,quality:materialQuality(),duration:video.duration,time:contiguous,prefetch:true,keep:materialPins()});}
 }catch(e){}
},750);
async function ensureExport(c,cancelled=()=>false){
 materialExporting=true;pauseMaterial();
 const url=video.url,quality=$('quality').value;
 try{
  let keep=materialPins();
  for(let count=0;count<64;count++){
   if(cancelled())throw Error('已取消准备选区');
   if(video.url!==url||$('quality').value!==quality)throw Error('视频或清晰度已改变，请重新选择。');
   const result=await api('player/selection',{url:video.url,quality:$('quality').value,start:c.start,end:c.end});
   if(result.ready)return result.segments;
   keep=[...new Set(keep.concat(result.segments.map(s=>s.id)))];
   $('buffer-status').textContent='正在补齐选区缺少的素材，已缓冲部分直接复用…';$('save-prepare-status').textContent=`正在准备 ${c.name||'当前选区'}：${time(result.gap)} / ${time(c.end)}。已缓冲部分直接复用；关闭此窗口可取消。`;
   let state=await api('player/request',{url:video.url,quality:$('quality').value,duration:video.duration,time:result.gap,keep,export:true});
   while(state.state==='working'){await sleepUI(250);if(cancelled())throw Error('已取消准备选区');state=await api('player/status',{});}
   if(state.state!=='ready')throw Error(state.message);
  }
  throw Error('选区过长，请缩短后下载。');
 }finally{materialExporting=false;}
}
