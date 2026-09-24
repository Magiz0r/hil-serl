'use strict';
const byId=id=>document.getElementById(id);
let current=null, connected=false, refreshing=false, lastReceived=0;
let awaiting=null, inflight=null, requestSequence=0, localError='', lastStateKey='', lastError='', episodeSignature='';
let homeToSet='set_home';
const outcomeLabels={success:'成功',failure:'失败',discard:'作废',incomplete:'不完整',unlabeled:'待标注'};
const outcomeColors={success:'good',failure:'bad',discard:'muted',incomplete:'warn',unlabeled:'muted'};
const taskLabel=name=>typeof name==='string' && name.trim()?name:'未记录任务';
const episodeTask=e=>taskLabel(e.task_display_name || e.task);
const episodeTaskKey=e=>e.task_id || e.task || '__unknown__';
const episodeName=e=>/^episode_\d+$/.test(e.name || '') && e.session?'episode_'+e.session.replace(/^pilot_/,'')+'_'+e.name.slice(8):e.name || e.id || '未知记录';
const duration=seconds=>Number.isFinite(seconds)?`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`:'—';
const clockTime=seconds=>Number.isFinite(seconds)?new Date(seconds*1000).toLocaleTimeString('zh-CN',{hour12:false}):'—';
function write(id,value){const element=byId(id);const text=String(value ?? '—');if(element.textContent!==text) element.textContent=text;}
function badge(id,label,color){const element=byId(id);element.className='badge '+color;element.replaceChildren(document.createElement('i'),document.createTextNode(label));}
function log(message,level='info') {
  const box=byId('logs');
  const bottom=box.scrollHeight-box.scrollTop-box.clientHeight<30;
  const row=document.createElement('div');row.className='log-entry';
  const stamp=document.createElement('time');stamp.textContent=clockTime(Date.now()/1000);
  const content=document.createElement('span');content.className=level;content.textContent=message;
  row.append(stamp,content);box.append(row);
  if(box.children.length>200) box.firstElementChild.remove();
  write('log-total',box.children.length);
  if(byId('log-follow').checked && (bottom || byId('pane-logs').hidden)) box.scrollTop=box.scrollHeight;
}
function feedback(message,level='info'){write('command-feedback',message);byId('command-feedback').className='command-feedback '+level;}
function service(name,label,color,detail) {
  const element=byId('service-'+name);element.className='service '+color;
  element.querySelector('b').textContent=label;
  element.title=detail || label;
}
function modeFor(s) {
  if(!connected) return ['offline','连接中断','warn','页面连接中断，状态已过期。控制状态未知；可用 SpaceMouse 双键退出控制。'];
  if(s.fatal) return ['error','已停止','bad','录制器异常退出。请重启专用采集会话。'];
  if(s.runtime?.phase==='disconnected') return ['disconnected','控制未连接','muted','相机与任务配置可用。完成现场准备后，点击“连接控制”。'];
  if(s.runtime?.phase==='connecting') return ['connecting','连接控制中','info','正在检查 NUC、Franka Desk 和控制器实际状态；可按 Stop 取消。'];
  if(s.runtime?.phase==='disconnecting') return ['disconnecting','断开控制中','warn','正在结束本网页的控制会话，等待进程退出。'];
  if(s.runtime?.phase==='error') return ['error','连接异常','bad',s.runtime.error || s.reason || '控制会话异常'];
  if(s.pending?.action==='lock' || awaiting?.action==='lock') return ['saving',s.recording?'正在保存':'正在停止','warn',s.recording?'正在停用输入，等待机械臂与夹爪确认后保存…':'正在停用输入，等待控制器保持当前位置…'];
  if(s.pilot?.mode==='homing') {const name=s.pilot.active_home==='custom'?'自定义 Home':'DROID Home';return ['homing','返回 '+name,'info',(s.pilot.joint_phase==='checking'?'正在检查返回路径：':'正在返回 ')+name+' · SpaceMouse 已停用，可按 Stop 中断。'];}
  if(s.pilot?.custom_home?.saving) return ['pending','保存自定义 Home','info','正在保存关节目标并准备自定义返回控制器…'];
  if(!s.ready) return ['loading','暂不可采集','warn',s.reason || '正在等待设备数据…'];
  if(s.recording) return ['running','采集中','good',s.pilot?.mode==='waiting_for_center'?'正在采集 · 请松开 SpaceMouse 旋钮回中。':'正在采集 · SpaceMouse 可操作，完成后点击 Success、Fail 或 Stop。'];
  if((s.pending?.action || awaiting?.action || inflight?.action || '').startsWith('gripper_')) return ['preparing','夹爪准备中','info','正在操作夹爪 · 不录制，机械臂保持当前位置。可按 Stop 中断。'];
  if(awaiting || s.pending) return ['pending','等待确认','info','请求已发送，正在等待控制器确认…'];
  return ['idle','就绪','good','保持当前位置 · SpaceMouse 已停用，摆好物体后开始。'];
}
function renderControls() {
  const s=current || {}, p=s.pilot || {};
  const busy=!!awaiting || !!inflight || !!s.pending || !!p.custom_home?.saving;
  const canAct=connected && s.ready && !s.fatal && !s.recording && p.mode==='locked' && !busy;
  byId('start').disabled=!canAct || PilotCatalogUI.blocksStart();
  byId('mobile-start').disabled=byId('start').disabled;
  byId('home').disabled=!(canAct && p.home_set);
  byId('set_home').disabled=!canAct || p.home_kind==='official_joint';
  byId('set-custom-home').disabled=!canAct || !p.custom_home;
  byId('custom-home').disabled=!(canAct && p.custom_home?.available);
  for(const id of ['gripper-open','gripper-close']) byId(id).disabled=!canAct || !s.telemetry?.gripper?.ok || !s.telemetry?.gripper?.preparation_supported;
  const gripperBusy=(s.pending?.action || awaiting?.action || inflight?.action || '').startsWith('gripper_');
  const gripperText=!connected || s.fatal?'夹爪反馈已过期':s.last_command_error? s.last_command_error:s.recording?'采集中请使用 SpaceMouse 左 / 右键':!s.ready?'连接控制并等待设备就绪':!s.telemetry?.gripper?.preparation_supported?'请断开并重新连接控制，启用夹爪按钮':p.mode==='homing'?'等待 Home 完成':gripperBusy?'动作中，等待到位或接触反馈…':s.telemetry?.gripper?.detail || '等待夹爪反馈';
  write('gripper-status',gripperText+(connected && s.telemetry?.gripper?.ok && Number.isFinite(s.telemetry.gripper_position)?' · '+s.telemetry.gripper_position+'/255':''));
  // Stop can preempt an in-flight Start/Home, and remains reachable after a network failure.
  const finishBusy=inflight?.action==='lock' || awaiting?.action==='lock';
  for(const id of ['finish','top-finish','mobile-finish']) byId(id).disabled=!!s.fatal || finishBusy;
  if(!current && !localError) for(const id of ['finish','top-finish','mobile-finish']) byId(id).disabled=true;
  const mode=modeFor(s);
  badge('capture-badge',mode[1],mode[2]);write('status',mode[3]);write('mode-label',mode[1]);
  write('mobile-state',mode[1]);
  document.body.dataset.state=mode[0];
  byId('start').title=canAct?'开始记录，然后启用 SpaceMouse':p.mode==='homing'?'Home 期间不能开始采集':'等待设备就绪、保持当前位置且前一个操作完成';
  byId('home').title=p.home_set?'回零不会自动开合夹爪；请先确认返回路径畅通':'尚未设置 Home';
  for(const [id,active,done] of [['phase-ready',mode[0]==='idle',mode[0]==='running'||mode[0]==='saving'],['phase-record',mode[0]==='running',mode[0]==='saving'],['phase-save',mode[0]==='saving',false]]) byId(id).className=active?'active':done?'done':'';
  const canMark=connected && s.recording && !s.fatal && !busy;
  for(const id of ['finish-success','finish-failure','mobile-success','mobile-failure']) byId(id).disabled=!canMark;
  PilotCatalogUI.render(s);
  return mode;
}
function renderHome(s) {
  const p=s.pilot || {},official=p.home_kind==='official_joint';
  const custom=p.custom_home;
  byId('custom-home-panel').hidden=!!p.mode && !official;
  write('custom-home-status',!connected?'连接中断 · 目标状态已过期':!custom?'连接后读取自定义目标':custom.saving?'正在保存自定义目标…':custom.error?'自定义目标不可用：'+custom.error:custom.available?(custom.persistent?'已保存 · 重启后保留':'已设置 · 本次会话'):'尚未设置 · 在所需位置点击下方按钮');
  write('custom-home-target',custom?.q?custom.q.map((q,i)=>`J${i+1} ${(q*180/Math.PI).toFixed(1)}°`).join(' · '):'尚未设置');
  write('custom-home-feedback',custom?.saved_unix?'保存于 '+new Date(custom.saved_unix*1000).toLocaleString('zh-CN')+(typeof custom.joint_error_rad==='number'?` · 当前最大关节残差 ${custom.joint_error_rad.toFixed(4)} rad`:''):'设置只保存当前位置，不会执行回位');
  if(!p.mode && s.runtime?.home_source==='droid_default'){
    write('home_status','DROID 默认关节目标 · 等待控制连接');
    write('home-target',s.runtime.home_joint_degrees.map((x,i)=>`J${i+1} ${x}°`).join(' · '));
    write('home-error','尚无实测关节反馈');byId('set_home').hidden=true;byId('home_help').hidden=true;byId('official_home_help').hidden=false;return;
  }
  byId('set_home').hidden=official;byId('home_help').hidden=official;byId('official_home_help').hidden=!official;
  write('home_status',official?(p.home_source==='droid_default'?'DROID 默认关节目标':'固定关节目标 · 当前控制器配置'):p.home_set?'自定义末端目标 · 仅本次控制会话':'尚未设置 Home');
  if(official && Array.isArray(p.home_profile_seconds))
    byId('home_status').textContent+=' · 轨迹 '+p.home_profile_seconds.join('–')+' s';
  const q=p.home?.q;
  write('home-target',official && Array.isArray(q)?q.map((x,i)=>`J${i+1} ${(x*180/Math.PI).toFixed(1)}°`).join(' · '):p.home?.xyz?'XYZ '+p.home.xyz.map(x=>(x*1000).toFixed(1)).join(', ')+' mm':'等待控制器提供目标');
  write('home-error',typeof p.home_joint_error_rad==='number'?`当前最大关节残差 ${p.home_joint_error_rad.toFixed(4)} rad${connected?'':' · 数据已过期'}`:'当前控制器未提供关节残差');
}
function renderServices(s,mode) {
  const t=s.telemetry || {};
  for(const name of ['arm','gripper','control']) {
    const v=t[name];
    service(name,!connected?'过期':s.fatal?'未知':!v?'等待':v.ok?'在线':'异常',!connected?'warn':s.fatal?'warn':!v?'muted':v.ok?'good':'bad',v?`${v.detail} · ${v.age==null?'未知':Math.round(v.age*1000)+' ms'}`:'等待独立状态反馈');
  }
  const ages=s.camera_ages || {};
  const count=['external','wrist'].filter(name=>!s.camera_errors?.[name] && typeof ages[name]==='number' && ages[name]>=0 && ages[name]<=.35).length;
  service('cameras',!connected?'过期':s.fatal?'未知':count+'/2',connected && !s.fatal && count===2?'good':'warn','相机采集新鲜度；每路图像连接状态见画面');
  service('motion',!connected || s.fatal?'未知':({locked:'保持',manual:'手动',waiting_for_center:'等回中',homing:'回零'})[s.pilot?.mode] || '等待',mode[2]);
  write('robot-detail',!connected || s.fatal?'反馈已过期':t.arm?.detail || '等待独立状态');
  write('gripper-detail',!connected || s.fatal?'反馈已过期':t.gripper?`${t.gripper.detail}${typeof t.gripper_position==='number'?' · 位置 '+t.gripper_position+'/255':''}`:'等待独立状态');
  write('quality-detail',!connected || s.fatal?'状态未知':s.ready?'数据流通过检查':s.reason || '等待检查');
}
function renderEpisodes(force=false) {
  const allEpisodes=current?.episodes || [];
  const taskFilter=byId('record-task-filter');
  const tasks=[...new Map(allEpisodes.map(e=>[episodeTaskKey(e),episodeTask(e)])).entries()];
  if(taskFilter.dataset.names!==JSON.stringify(tasks)){
    const previous=taskFilter.value;taskFilter.replaceChildren(new Option('全部任务','all'),...tasks.map(([id,name])=>new Option(name,id)));taskFilter.value=tasks.some(([id])=>id===previous)?previous:'all';taskFilter.dataset.names=JSON.stringify(tasks);
  }
  const episodes=allEpisodes.filter(e=>taskFilter.value==='all' || episodeTaskKey(e)===taskFilter.value);
  const signature=JSON.stringify([episodes,byId('episode-filter').value,taskFilter.value]);
  if(!force && signature===episodeSignature) return;
  episodeSignature=signature;
  const counts={success:0,failure:0,unlabeled:0,incomplete:0,discard:0};
  episodes.forEach(e=>{if(e.outcome in counts) counts[e.outcome]++;});
  write('episode-total',episodes.length);write('success-count',counts.success);write('failure-count',counts.failure);
  write('unlabeled-count',counts.unlabeled);write('other-count',counts.incomplete+counts.discard);
  write('success-rate',counts.success+counts.failure?Math.round(counts.success/(counts.success+counts.failure)*100)+'%':'—');
  byId('export-summary').disabled=!episodes.length;
  const selected=episodes.filter(e=>byId('episode-filter').value==='all' || e.outcome===byId('episode-filter').value).slice().reverse();
  byId('empty-records').hidden=selected.length>0;
  byId('empty-records').querySelector('strong').textContent=episodes.length?'没有符合筛选的记录':'等待第一条演示';
  byId('empty-records').querySelector('p').textContent=episodes.length?'选择其他任务或结果查看记录。':'点击 Start 开始；Stop 后，记录将显示在这里。';
  byId('episodes').replaceChildren(...selected.map(episode=>{
    const row=document.createElement('tr');
    const task=document.createElement('td');task.className='episode-task';
    const name=document.createElement('strong');name.textContent=episodeTask(episode);task.append(name);
    if(episode.layout){const layout=document.createElement('small');layout.textContent='Layout · '+episode.layout.name;task.append(layout);}
    row.append(task);
    const identity=document.createElement('td');identity.className='episode-name';identity.textContent=episodeName(episode);identity.title=episode.directory || episodeName(episode);row.append(identity);
    const values=[episode.created_unix?new Date(episode.created_unix*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—',episode.samples,duration(episode.duration_seconds)];
    values.forEach(value=>{const td=document.createElement('td');td.textContent=value ?? '—';row.append(td);});
    const outcome=document.createElement('td'), pill=document.createElement('span');
    pill.className='result-pill '+(outcomeColors[episode.outcome] || 'muted');pill.textContent=outcomeLabels[episode.outcome] || episode.outcome;outcome.append(pill);row.append(outcome);
    const action=document.createElement('td'),button=document.createElement('button');
    button.className='text-button';button.textContent=episode.outcome==='unlabeled'?'标注 ↗':'详情 ↗';button.setAttribute('aria-label','查看 '+episodeName(episode)+' 详情');button.addEventListener('click',()=>showEpisode(episode));
    action.append(button);row.append(action);return row;
  }));
}
function renderEpisodeDetail(episode) {
  write('episode-dialog-title',episodeName(episode));
  const fields=[['任务',episodeTask(episode)],['Prompt',episode.prompt || '未提供'],['Layout',episode.layout?episode.layout.name+(episode.layout.ood?' · OOD':''):'未关联'],['开始时间',episode.created_unix?new Date(episode.created_unix*1000).toLocaleString('zh-CN'):'未提供'],['结果',outcomeLabels[episode.outcome] || episode.outcome],['备注',episode.notes || '—'],['采样 / 时长',`${episode.samples} / ${duration(episode.duration_seconds)}`],['原因',episode.reason || '—'],['数据目录',episode.directory || (current?.directory || '')+'/'+(episode.storage_name || episode.name)]];
  byId('episode-detail').replaceChildren(...fields.map(([label,value])=>{const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;row.append(dt,dd);return row;}));
}
function showEpisode(episode) {
  renderEpisodeDetail(episode);
  PilotRecordsUI.open(episode);
  byId('episode-dialog').showModal();
}
function render() {
  const s=current || {}, mode=renderControls();
  const task=s.task?.name;
  write('task-name',task || '等待任务配置');
  write('task-prompt',s.task?.success_definition || '可创建任务并定义成功标准');
  write('guide-instruction',s.prompt || s.task?.prompt || '选择任务、摆好场景，开始采集。');
  write('sample-hz',s.sample_hz || '—');
  write('session-label',PilotAPI.isDemo?'DEMO SESSION':s.directory?.split('/').pop() || '等待采集会话');
  write('current-episode',s.current_episode || '等待下一条采集');
  write('sample-count',Number(s.sample_count || 0).toLocaleString('en-US'));
  write('elapsed',duration(Number(s.elapsed_seconds || 0)));
  write('directory',s.directory || '等待连接');
  renderHome(s);renderServices(s,mode);renderEpisodes();PilotRecordsUI.update(s.episodes);PilotCameras.update(s,connected);
  byId('runtime-controls').hidden=!s.runtime;
  document.querySelector('.records-scope').textContent=s.runtime?'已保存的历史记录':'当前采集会话';
  if(s.runtime){
    write('runtime-state',({disconnected:'控制会话未连接',connecting:'正在连接真实控制器…',connected:'真实控制会话已连接',disconnecting:'正在断开控制…',error:'控制会话异常'})[s.runtime.phase] || s.runtime.phase);
    byId('runtime-connect').disabled=!connected || !s.runtime.can_connect;
    byId('runtime-disconnect').disabled=!connected || !s.runtime.can_disconnect || !!s.recording;
    byId('runtime-cameras').disabled=!connected || !['disconnected','error'].includes(s.runtime.phase);
  }
  const ordinaryWait=['disconnected','connecting','disconnecting'].includes(s.runtime?.phase);
  const cameraError=Object.entries(s.camera_errors || {}).filter(([,error])=>error).map(([name,error])=>name+': '+error).join('；');
  const error=localError || s.last_command_error || s.pilot?.error || s.pilot?.custom_home?.error || s.runtime?.error || cameraError || (s.ready===false && !ordinaryWait?s.reason:'');
  byId('error-banner').hidden=!error;write('error',error);
  if(error && error!==lastError) log(error,'bad');lastError=error;
  const stateKey=[mode[0],s.pilot?.mode,s.current_episode,s.episodes?.length].join('|');
  if(stateKey!==lastStateKey && current) {log(mode[3],mode[2]);lastStateKey=stateKey;}
  badge('connection',PilotAPI.isDemo?'DEMO':connected?'网页在线':'网页断线',PilotAPI.isDemo?'warn':connected?'good':'warn');
}
function reconcile(s) {
  if(!awaiting) return;
  const p=s.pilot || {},pending=awaiting;
  const rejected=(s.last_command_error && s.last_command_error!==pending.previousError) || (p.error && p.command_id>pending.beforeId);
  if(rejected) {
    localError=s.last_command_error || p.error;feedback('操作未完成：'+localError,'bad');awaiting=null;return;
  }
  const isGripper=pending.action.startsWith('gripper_');
  const ack=typeof p.command_id==='number' && p.command_id>pending.beforeId && p.command===(isGripper?'lock':pending.action) && !s.pending && (!isGripper || (s.gripper_preparation?.id===p.command_id && s.gripper_preparation?.action===pending.action.slice(8) && s.gripper_preparation?.complete));
  const completed=ack && (pending.action==='start'?s.recording:pending.action==='lock'?p.mode==='locked' && !s.recording:['home','custom_home'].includes(pending.action)?['homing','locked'].includes(p.mode):pending.action==='set_custom_home'?!p.custom_home?.saving && p.custom_home?.available:true);
  if(completed) {
    awaiting=null;
    const saved=s.last_saved_episode || s.episodes?.find(e=>(e.storage_name || e.name)===pending.episodeName) || s.episodes?.at(-1);
    const message=isGripper?(s.gripper_preparation.contact?'夹爪已接触物体，请确认夹持状态':'夹爪已到目标位置')+' · 未录制，准备好后再 Start':pending.action==='lock'?(pending.wasRecording?'记录已保存 · '+(outcomeLabels[saved?.outcome] || '等待记录'):'已停用输入 · 保持当前位置'):pending.action==='start'?'采集已开始 · 请先松开旋钮回中':pending.action==='home'?'控制器已接收 DROID Home，等待回位反馈':pending.action==='custom_home'?'控制器已接收自定义 Home，等待回位反馈':pending.action==='set_custom_home'?'自定义 Home 已保存':'Home 目标已更新';
    feedback(message,'good');log(message,'good');
  } else if(performance.now()-pending.at>(pending.action==='set_custom_home' || isGripper?12000:6500)) {
    awaiting=null;localError='控制器未在预期时间内确认操作。请检查当前状态；可按 Stop 停用输入。';feedback('操作完成状态未确认','warn');
  }
}
async function refresh() {
  if(refreshing) return;
  refreshing=true;
  try {
    const s=await PilotAPI.status();
    if(!s || typeof s.ready!=='boolean' || !Array.isArray(s.episodes)) throw Error('状态接口返回格式不完整');
    const wasConnected=connected;
    if(current?.directory && s.directory!==current.directory) {awaiting=null;inflight=null;log('采集会话已切换，重新读取状态','warn');}
    const previousMode=current?.pilot?.mode;
    current=s;connected=true;lastReceived=performance.now();
    if(!wasConnected) {log(PilotAPI.isDemo?'模拟会话已连接':'录制器连接已恢复；已重新同步实际状态','good');if(localError.startsWith('连接失败')) localError='';}
    reconcile(s);
    if(previousMode==='homing' && s.pilot?.mode==='locked' && !s.pilot?.error) {
      const error=s.pilot.active_home_joint_error_rad ?? s.pilot.home_joint_error_rad;
      const name=s.pilot.active_home==='custom'?'自定义 Home':'DROID Home';
      const message=typeof error==='number'?`${name} 已结束，保持当前位置 · 最大关节残差 ${error.toFixed(4)} rad`:name+' 已结束，保持当前位置';
      feedback(message,'info');log(message,'info');
    }
    render();
  } catch(error) {
    connected=false;localError='连接失败：'+error.message;
    if(awaiting && performance.now()-awaiting.at>6500) awaiting=null;
    render();
  } finally {refreshing=false;}
}
async function sendPilotCommand(command) {
  const action=command.startsWith('finish')?'lock':command;
  if(action==='lock') {if(inflight?.action==='lock' || awaiting?.action==='lock') return;
    if(command!=='finish' && (!connected || !current?.recording || current.pending || awaiting || inflight))return;}
  else if(!connected || !current?.ready || current.recording || current.pilot?.mode!=='locked' || current.pilot?.custom_home?.saving || current.pending || inflight || awaiting) return;
  if(action==='home' && !current.pilot.home_set) return;
  if(action==='start' && PilotCatalogUI.blocksStart()) return;
  if(action.startsWith('gripper_') && (!current.telemetry?.gripper?.ok || !current.telemetry?.gripper?.preparation_supported))return;
  if(action==='set_home' && current.pilot.home_kind==='official_joint') return;
  if(['set_custom_home','custom_home'].includes(action) && !current.pilot.custom_home)return;
  if(action==='custom_home' && !current.pilot.custom_home.available)return;
  const id=++requestSequence;
  const label={start:'Start',lock:command==='finish_success'?'Success':command==='finish_failure'?'Fail':'Stop',home:'DROID Home',set_home:'设置 Home',custom_home:'返回自定义 Home',set_custom_home:'保存自定义 Home',gripper_open:'打开夹爪',gripper_close:'闭合夹爪'}[action];
  inflight={id,action};awaiting=null;localError='';
  const pending={episodeName:current?.current_episode,action,at:performance.now(),beforeId:current?.pilot?.command_id ?? -1,wasRecording:!!current?.recording,previousError:current?.last_command_error};
  feedback(label+' 请求发送中…');log(label+'：发送请求');render();
  try {
    await PilotAPI.command(command);
    if(id!==requestSequence) return;
    awaiting=pending;feedback(label+' 请求已送达，等待控制器确认');
  } catch(error) {
    if(id!==requestSequence) return;
    localError='请求未确认：'+error.message;feedback(label+' 未确认，请查看当前设备状态','bad');log(localError,'bad');
  } finally {
    if(id===requestSequence) {inflight=null;render();refresh();}
  }
}
function finish() {
  if(current?.runtime?.phase==='connecting'){
    byId('runtime-disconnect').click();feedback('正在取消控制连接，等待启动流程退出');return;
  }
  if(current?.runtime?.phase==='disconnecting'){feedback('控制会话正在断开');return;}
  sendPilotCommand('finish');
}
byId('start').addEventListener('click',()=>sendPilotCommand('start'));
byId('mobile-start').addEventListener('click',()=>sendPilotCommand('start'));
for(const id of ['finish','top-finish','mobile-finish']) byId(id).addEventListener('click',finish);
byId('home').addEventListener('click',()=>sendPilotCommand('home'));
byId('gripper-open').addEventListener('click',()=>sendPilotCommand('gripper_open'));
byId('gripper-close').addEventListener('click',()=>sendPilotCommand('gripper_close'));
byId('set_home').addEventListener('click',()=>{homeToSet='set_home';write('confirm-home-title','覆盖本次会话的 Home？');write('confirm-home-message','将当前实测位姿保存为本次会话的 Home。设置本身不会移动机械臂。');byId('confirm-home').showModal();});
byId('custom-home').addEventListener('click',()=>sendPilotCommand('custom_home'));
byId('set-custom-home').addEventListener('click',()=>{
  if(!current?.pilot?.custom_home?.q){sendPilotCommand('set_custom_home');return;}
  homeToSet='set_custom_home';write('confirm-home-title','覆盖自定义 Home？');write('confirm-home-message','将当前七个实测关节角保存为新的自定义 Home，覆盖此前保存的自定义目标。DROID Home 保持固定。设置本身不会移动机械臂。');byId('confirm-home').showModal();
});
byId('confirm-set-home').addEventListener('click',()=>sendPilotCommand(homeToSet));
byId('close-episode').addEventListener('click',()=>byId('episode-dialog').close());
byId('retry').addEventListener('click',()=>{localError='';refresh();});
for(const [id,action] of [['runtime-connect','connect'],['runtime-disconnect','disconnect'],['runtime-cameras','retry_cameras']]){
  byId(id).addEventListener('click',async()=>{
    byId(id).disabled=true;
    try{await PilotAPI.runtime(action);localError='';log('控制服务请求：'+action);await refresh();}
    catch(error){localError=error.message;render();}
  });
}
for(const id of ['finish-success','mobile-success'])byId(id).addEventListener('click',()=>sendPilotCommand('finish_success'));
for(const id of ['finish-failure','mobile-failure'])byId(id).addEventListener('click',()=>sendPilotCommand('finish_failure'));
byId('episode-filter').addEventListener('change',()=>renderEpisodes(true));
byId('record-task-filter').addEventListener('change',()=>renderEpisodes(true));
function activateTab(name) {
  document.querySelectorAll('[data-tab]').forEach(button=>{const selected=button.dataset.tab===name;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;byId('pane-'+button.dataset.tab).hidden=!selected;});
}
const tabs=Array.from(document.querySelectorAll('[data-tab]'));
tabs.forEach((button,index)=>{
  button.addEventListener('click',()=>activateTab(button.dataset.tab));
  button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const target=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;activateTab(tabs[target].dataset.tab);tabs[target].focus();}});
});
byId('logs').addEventListener('scroll',()=>{const box=byId('logs');byId('log-follow').checked=box.scrollHeight-box.scrollTop-box.clientHeight<30;});
byId('log-follow').addEventListener('change',()=>{if(byId('log-follow').checked) byId('logs').scrollTop=byId('logs').scrollHeight;});
byId('copy-directory').addEventListener('click',async()=>{
  try {await navigator.clipboard.writeText(current?.directory || '');feedback('数据目录已复制','good');}
  catch(_){feedback('无法访问剪贴板，请在会话信息中选择目录文字复制','warn');}
});
byId('export-summary').addEventListener('click',()=>{
  const data={demo:PilotAPI.isDemo,directory:current?.directory,task:current?.task,episodes:current?.episodes,exported_at:new Date().toISOString()};
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download=(PilotAPI.isDemo?'demo-':'')+'capture-summary.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
document.querySelectorAll('[data-fullscreen]').forEach(button=>button.addEventListener('click',async()=>{
  try {if(document.fullscreenElement) await document.exitFullscreen();else await byId(button.dataset.fullscreen).requestFullscreen();}
  catch(_){feedback('当前浏览器不支持相机全屏','warn');}
}));
if(PilotAPI.isDemo) {
  byId('demo-banner').hidden=false;byId('demo-state').value=PilotDemo.scenario;
  write('footer-mode','DEMO · 未连接真实设备');
  byId('demo-state').addEventListener('change',()=>{++requestSequence;awaiting=null;inflight=null;localError='';PilotDemo.reset(byId('demo-state').value);log('切换演示场景：'+byId('demo-state').selectedOptions[0].text);refresh();});
}
setInterval(()=>{
  const seconds=lastReceived?(performance.now()-lastReceived)/1000:null;
  write('updated',seconds==null?'等待首次状态':connected?'状态同步 · 刚刚更新':`数据已过期 · ${Math.floor(seconds)} 秒前更新`);
},500);
PilotCatalogUI.init();
PilotRecordsUI.init();
PilotCameras.start();
setInterval(refresh,500);
refresh();
