'use strict';
// This adapter has no fetch, sockets, storage or device imports.
const PilotDemo = (() => {
  let state, scenario = 'idle', began = 0, timers = [], episodeCounter=0;
  const task = {id:'task_demo1',name:'block_into_cup',display_name:'方块入杯',prompt:'Pick up the block and place it inside the cup.',success_definition:'方块留在杯内，夹爪松开并离开。'};
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));
  function reset(name) {
    timers.forEach(clearTimeout); timers = []; scenario = name;
    const now = Date.now() / 1000;
    state = {
      ready:true, recording:false, sample_count:0, elapsed_seconds:0, reason:null, fatal:false,
      directory:'DEMO / block_into_cup / pilot_preview', task, session_started_unix:now-480, sample_hz:10,
      pilot:{mode:'locked', home_set:true, home_kind:'official_joint', command_id:0, command:'lock',
        home_source:'droid_default',home:{q:[0,-Math.PI/5,0,-4*Math.PI/5,0,3*Math.PI/5,0], xyz:[.3,0,.5]}, home_joint_error_rad:.002, joint_phase:'idle',active_home:'droid',
        custom_home:{available:false,saving:false,persistent:false,q:null,saved_unix:null,error:null}},
      pending:null, camera_ages:{external:.026,wrist:.019},
      telemetry:{arm:{ok:true,age:.014,detail:'状态在线'},control:{ok:true,age:.022,detail:'遥操作在线'},
        gripper:{ok:true,age:.033,detail:'保持',preparation_supported:true},gripper_position:12,gripper_fault:0},
      episodes:[
        {name:'episode_0001',task:task.name,outcome:'success',samples:246,duration_seconds:24.6,created_unix:now-360},
        {name:'episode_0002',task:task.name,outcome:'failure',samples:183,duration_seconds:18.3,created_unix:now-290},
        {name:'episode_0003',task:task.name,outcome:'success',samples:312,duration_seconds:31.2,created_unix:now-180},
        {name:'episode_0004',task:task.name,outcome:'unlabeled',samples:208,duration_seconds:20.8,created_unix:now-90}
      ]
    };
    state.catalog={tasks:[structuredClone(task),{id:'task_demo2',name:'插入连接器',display_name:'插入连接器',prompt:'Insert the connector into the socket.',success_definition:'连接器完整插入插座。'}],layouts:[{id:'layout_demo1',task_id:task.id,name:'标准摆放',domain:'real',ood:false},{id:'layout_demo2',task_id:task.id,name:'侧向摆放',domain:'real',ood:true}],task_id:task.id,layout_id:null,prompt:task.prompt};
    state.prompt=task.prompt;
    state.episodes.forEach((e,i)=>{e.name='episode_DEMO_history_'+String(i+1).padStart(4,'0');});
    if (name === 'empty') state.episodes = [];
    if (name === 'loading') {
      state.ready=false; state.reason='正在等待相机与控制状态'; state.telemetry={}; state.camera_ages={}; state.pilot={};
    }
    if (name === 'running' || name === 'saving') {
      began = performance.now()-12400; state.recording=true; state.pilot.mode='manual'; state.current_episode='episode_0005';
      if (name === 'saving') state.pending={action:'lock',id:1};
    }
    if (name === 'homing') {state.pilot.mode='homing'; state.pilot.joint_phase='active'; state.pilot.home_joint_error_rad=.143;}
    if (name === 'error') {
      state.ready=false; state.reason='wrist: camera missing or stale'; state.camera_ages.wrist=2.4;
      state.episodes.push({name:'episode_0005',task:task.name,outcome:'incomplete',samples:83,duration_seconds:8.3,created_unix:now-20,reason:'wrist: camera missing or stale'});
    }
  }
  async function status() {
    if (scenario === 'offline') throw Error('演示：与录制器的连接已断开');
    if (state.recording) {
      state.elapsed_seconds=(performance.now()-began)/1000;
      state.sample_count=Math.floor(state.elapsed_seconds*10);
    }
    return structuredClone(state);
  }
  async function command(value) {
    if (scenario === 'offline') throw Error('演示：请求未送达');
    const isFinish = value.startsWith('finish');
    if(isFinish && value!=='finish' && !state.recording)throw Error('请在采集记录中补充标注');
    if (!isFinish && (!state.ready || state.recording || state.pilot.mode !== 'locked' || state.pending)) throw Error('请先 Finish，等待机械臂保持不动');
    if (value==='custom_home' && !state.pilot.custom_home.available) throw Error('请先设置自定义 Home');
    timers.forEach(clearTimeout); timers=[];
    const action=isFinish?'lock':value;
    const id=state.pilot.command_id+1;
    state.pending={action,id};
    later(() => {
      state.pending=null; state.pilot.command_id=id; state.pilot.command=action.startsWith('gripper_')?'lock':action;
      if (isFinish) {
        if (state.recording) {
          state.episodes.push({name:state.current_episode,task:state.task.name,task_id:state.task.id,prompt:state.prompt,layout:structuredClone(state.layout || null),outcome:value.split('_')[1] || 'unlabeled',
            samples:state.sample_count,duration_seconds:state.elapsed_seconds,created_unix:Date.now()/1000-state.elapsed_seconds});
        }
        state.recording=false; state.sample_count=0; state.elapsed_seconds=0; state.current_episode=null;
        state.pilot.mode='locked'; state.pilot.joint_phase='idle';
      } else if (action.startsWith('gripper_')) {
        const closing=action==='gripper_close';
        state.gripper_preparation={id,action:closing?'close':'open',complete:true,contact:closing};
        state.telemetry.gripper_position=closing?165:0;
        state.telemetry.gripper.detail=closing?'闭合时接触物体':'已到目标位置';
      } else if (value === 'start') {
        began=performance.now(); state.recording=true; state.pilot.mode='waiting_for_center';
        state.current_episode='episode_DEMO_'+Date.now()+'_'+(++episodeCounter);
        later(()=>{state.pilot.mode='manual';},800);
      } else if (value === 'set_custom_home') {
        state.pilot.custom_home={available:true,saving:false,persistent:false,q:[.1,-.65,.08,-2.4,.05,1.9,.1],saved_unix:Date.now()/1000,joint_error_rad:0,error:null};
      } else if (value === 'home' || value === 'custom_home') {
        state.pilot.active_home=value==='home'?'droid':'custom';
        state.pilot.mode='homing'; state.pilot.joint_phase='active'; state.pilot.active_home_joint_error_rad=.12;
        later(()=>{state.pilot.mode='locked';state.pilot.joint_phase='idle';state.pilot.active_home_joint_error_rad=.002;
          if(value==='home')state.pilot.home_joint_error_rad=.002;else state.pilot.custom_home.joint_error_rad=.002;},4500);
      }
    },650);
    return {accepted:true};
  }
  async function catalog(data) {
    if(scenario==='offline')throw Error('演示：连接中断');
    if(state.recording || state.pending || state.pilot.mode==='homing')throw Error('请先停止采集');
    const c=state.catalog,id='demo_'+Date.now().toString(16),name=(data.name || '').trim();
    if(['task_create','layout_capture','layout_rename'].includes(data.action) && !name)throw Error('请输入名称');
    switch(data.action){
      case 'task_create':{
        if(!data.prompt?.trim())throw Error('请输入 Prompt');
        const t={id,name,display_name:name,prompt:data.prompt,success_definition:data.success_definition};c.tasks.push(t);c.task_id=id;c.layout_id=null;c.prompt=t.prompt;break;
      }
      case 'select':c.task_id=data.task_id;c.layout_id=data.layout_id || null;c.prompt=data.prompt;break;
      case 'task_update':{const t=c.tasks.find(t=>t.id===data.task_id);if(!t || !data.prompt?.trim())throw Error('任务或 Prompt 无效');t.prompt=data.prompt;t.success_definition=data.success_definition;if(c.task_id===t.id)c.prompt=t.prompt;break;}
      case 'task_delete':c.tasks=c.tasks.filter(t=>t.id!==data.task_id);c.task_id=null;c.layout_id=null;c.prompt='';break;
      case 'layout_capture':c.layouts.push({id,task_id:c.task_id,name,domain:'real',ood:!!data.ood});c.layout_id=id;break;
      case 'layout_rename':c.layouts.find(l=>l.id===data.layout_id).name=name;break;
      case 'layout_delete':c.layouts=c.layouts.filter(l=>l.id!==data.layout_id);c.layout_id=null;break;
      default:throw Error('unknown catalog action');
    }
    state.task=c.tasks.find(t=>t.id===c.task_id) || null;state.prompt=c.prompt;state.layout=c.layouts.find(l=>l.id===c.layout_id) || null;
    return structuredClone(c);
  }
  async function review(data) {
    const episode=state.episodes.find(e=>(e.id || e.name)===data.id);
    if(!episode)throw Error('记录不存在');
    if(data.action==='delete')state.episodes=state.episodes.filter(e=>e!==episode);
    else Object.assign(episode,{outcome:data.outcome,prompt:data.prompt,notes:data.notes});
    return structuredClone(state.episodes);
  }
  // Deliberately illustrative geometry, never presented as a real camera frame.
  function image(name) {
    const wrist = name==='wrist';
    const scene = wrist ? `
      <rect width="640" height="360" fill="#42484b"/>
      <path d="M0 60h640M0 150h640M0 240h640M0 330h640M90 0v360M240 0v360M390 0v360M540 0v360" stroke="#51575a"/>
      <ellipse cx="430" cy="221" rx="78" ry="74" fill="#151a1d" opacity=".35"/>
      <circle cx="420" cy="198" r="71" fill="#b2b7b5"/><circle cx="420" cy="198" r="60" fill="#5c686a"/><circle cx="420" cy="198" r="48" fill="#2b3639"/>
      <path d="m248 186 51-16 36 37-51 19z" fill="#d2a271"/><path d="m248 186 36 40v47l-36-39z" fill="#9a6842"/><path d="m284 226 51-19v47l-51 19z" fill="#bc8552"/>
      <path d="M224 0h200l-24 98-152 1z" fill="#abb3b6"/><rect x="255" y="63" width="137" height="65" rx="16" fill="#242a30"/>
      <path d="M263 112v58l-17 22h-27v-28l18-67zM381 112v58l17 22h27v-28l-18-67z" fill="#cad0d0"/>
      <path d="M226 164v28h20v-28M398 164v28h20v-28" fill="#191f22"/>
      <path d="M310 232h20m-10-10v20" stroke="#96b9ba" opacity=".6"/>
    ` : `
      <rect width="640" height="360" fill="#30383e"/>
      <path d="M0 0h640v119H0z" fill="#333d44"/><path d="M20 0v119M210 0v119M460 0v119" stroke="#495158"/>
      <path d="M0 121 488 96l152 122v142H0z" fill="#727a7b"/><path d="M0 290 640 257v103H0z" fill="#4c555a"/>
      <path d="M0 276 640 243M0 210 553 182M77 117 151 282M252 109 348 273M423 100 546 262" stroke="#858c8b" stroke-width="1" opacity=".55"/>
      <ellipse cx="350" cy="238" rx="72" ry="13" fill="#384045" opacity=".55"/>
      <path d="M399 179v51c0 16 66 16 66 0v-51" fill="#bac0ba"/><ellipse cx="432" cy="178" rx="33" ry="13" fill="#d4d8d0"/><ellipse cx="432" cy="179" rx="26" ry="9" fill="#626f6c"/>
      <path d="m294 218 28-8 25 12-30 10z" fill="#d2a271"/><path d="m294 218 23 14v27l-23-15z" fill="#99663e"/><path d="m317 232 30-10v26l-30 11z" fill="#bf8752"/>
      <ellipse cx="110" cy="207" rx="42" ry="13" fill="#28323a"/><path d="M88 203v-55h39v55z" fill="#d7dbda"/>
      <path d="m108 151 32-94" stroke="#222d35" stroke-width="34" stroke-linecap="round"/>
      <path d="m108 151 32-94" stroke="#c9d0d0" stroke-width="25" stroke-linecap="round"/>
      <circle cx="140" cy="57" r="24" fill="#19242d"/><circle cx="140" cy="57" r="13" fill="#89969b"/>
      <path d="m154 62 107 41" stroke="#d1d7d5" stroke-width="27" stroke-linecap="round"/>
      <circle cx="261" cy="105" r="20" fill="#202e38"/>
      <path d="m266 117 17 41" stroke="#c0cbcd" stroke-width="23" stroke-linecap="round"/>
      <path d="m273 163 12 25 21-8-10-26z" fill="#26313a"/>
      <path d="m285 182 1 21m16-29 12 22" stroke="#d3d7d7" stroke-width="8"/>
      <path d="M60 288h91m-45-10v20M502 258h67m-34-9v19" stroke="#b1bbb7" opacity=".6"/>
    `;
    return 'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">${scene}<rect x="0" y="0" width="640" height="360" fill="#122235" opacity=".12"/></svg>`);
  }
  reset(new URLSearchParams(location.search).get('scene') || 'idle');
  return {status,command,catalog,review,reset,image,get scenario(){return scenario;}};
})();
