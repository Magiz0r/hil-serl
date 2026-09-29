"""Run the shipped UI in Chrome; all live API requests use local fakes."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from html import unescape

ROOT = Path(__file__).parents[1] / 'portal' / 'static'
BOOTSTRAP = r'''
window.testErrors=[]; window.testCommands=[];
window.addEventListener('error',event=>testErrors.push(event.message));
window.addEventListener('unhandledrejection',event=>testErrors.push(String(event.reason)));
if (!('command' in HTMLButtonElement.prototype)) Object.defineProperty(HTMLButtonElement.prototype,'command',{get(){return '';}});
window.testState={ready:true,recording:false,episodes:[],sample_count:0,directory:'test-only',
 task:{name:'block_into_cup'},sample_hz:10,camera_ages:{external:.02,wrist:.03},telemetry:{gripper:{ok:true,age:.02,detail:'保持',preparation_supported:true},gripper_position:0},
 pilot:{mode:'locked',home_set:true,home_kind:'official_joint',home:{q:[0,-.78,0,-2.35,0,1.57,.78]},command_id:0,command:'lock',custom_home:{available:false,saving:false,q:null,persistent:true}},pending:null};
window.testOffline=false;window.testDelay=0;window.testReject=false;
window.testModelCommands=[];
window.testSettingsCommands=[];
window.testModels={initial_demo_episodes:1,demo_name:'single-demo.pkl',runs:[{id:'run1',name:'training-50',kind:'training',checkpoints:[{id:'latest',updates:13588,saved_unix:1790294046},{id:'early',updates:4677,saved_unix:1790292083}]}]};
window.testTrainingData={run_id:'run1',name:'training-50',generated_unix:1790294046,summary:{episodes:1,excluded_episodes:0,successes:0,last10_successes:0,last10_count:1,mean_return:0,transitions:95,automatic_fraction:.4},episodes:[{episode:1,outcome:'failure',return_:0,steps:95,automatic_interventions:38,reason:'force_limit',valid:true,provisional:true,success_last10:0}],learner:[{updates:13588,online_steps:6398,q:1.9,critic_loss:.002,actor_loss:-1.8,entropy:-2.8}]};
window.fetch=async(path,options={})=>{
 if(testOffline) throw Error('simulated disconnect');
 if(path==='/models')return {ok:true,json:async()=>testModels};
 if(path==='/training-settings'){
  const data=JSON.parse(options.body);testSettingsCommands.push(data);testState.training_settings=data;
  return {ok:true,json:async()=>data};
 }
 if(path.startsWith('/training-data?'))return {ok:true,json:async()=>testTrainingData};
 if(path==='/experiments'){
  const data=JSON.parse(options.body);testModelCommands.push(data);
  if(data.action==='prepare'){
   testState.experiment={phase:'ready',active:true,managed:true,can_prepare:false,run_id:'run1',mode:data.mode,model_label:data.model_id || 'fresh'};
   testState.training={available:true,session_id:'prepared',episode:0,phase:'paused',enabled:false,can_label:false,steps:0,gradient_updates:0,episode_return:0,message:'已准备，保持暂停'};
  }else{testState.experiment={phase:'ended',active:false,can_prepare:true};testState.training.available=false;}
  return {ok:true,json:async()=>({accepted:true})};
 }
 if(path==='/command'){
  const value=JSON.parse(options.body).command;testCommands.push(value);
  if(testReject) return {ok:false,status:400,json:async()=>({error:'controller rejected command'})};
  testState.pending={action:value.startsWith('finish')?'lock':value};
  setTimeout(()=>{
   testState.pending=null;
   testState.pilot.command_id++;
   testState.pilot.command=value.startsWith('finish')?'lock':value;
   if(value.startsWith('gripper_')){
    testState.pilot.command='lock';
    testState.gripper_preparation={id:testState.pilot.command_id,action:value.slice(8),complete:true,contact:value==='gripper_close'};
    testState.telemetry.gripper_position=value==='gripper_close'?162:0;
    testState.telemetry.gripper.detail=value==='gripper_close'?'闭合时接触物体':'已到目标位置';
   }
   if(value.startsWith('finish') && testState.recording) testState.episodes.push({name:'episode_0001',samples:12,outcome:value.split('_')[1]||'unlabeled',created_unix:100,duration_seconds:1.2,task:'block_into_cup'});
   testState.recording=value==='start';
   testState.pilot.mode=['home','custom_home'].includes(value)?'homing':value==='start'?'manual':'locked';
   if(value==='recovery_check'){testState.pilot.mode='recovery_check';testState.pilot.recovery_check.phase='approach';}
   if(['home','custom_home'].includes(value))testState.pilot.active_home=value==='home'?'droid':'custom';
   if(value==='set_custom_home')testState.pilot.custom_home={available:true,saving:false,persistent:true,q:[.1,-.65,.08,-2.4,.05,1.9,.1],saved_unix:Date.now()/1000,joint_error_rad:0};
  },testDelay);
  return {ok:true,json:async()=>({accepted:true})};
 }
 if(path==='/status')return {ok:true,json:async()=>structuredClone(testState)};
 if(path==='/training'){
  const value=JSON.parse(options.body);testCommands.push(value);
  if(value.action==='pause')testState.training.enabled=false;
  if(value.action==='resume')testState.training.enabled=true;
  if(['success','failure'].includes(value.action))testState.training.label=value.action;
  return {ok:true,json:async()=>({accepted:true})};
 }
 throw Error('unexpected network access: '+path);
};
'''


@unittest.skipUnless(shutil.which('google-chrome'), 'Chrome is not installed')
class PilotBrowserTests(unittest.TestCase):
    def test_duration_can_be_saved_while_running_without_starting_or_stopping(self):
        self.browser(r'''
testState.runtime={autoserl_demo:true,phase:'connected'};
testState.experiment={phase:'ready',active:true,managed:true,can_prepare:false,run_id:'run1',mode:'fresh'};
testState.training={available:true,session_id:'live',episode:0,phase:'running',enabled:true,can_label:true,time_limit_seconds:0,message:'运行中'};
testState.training_settings={time_limit_seconds:0};await settle(1200);
check(byId('training-time-limit').value==='0' && byId('training-live-message').textContent.includes('不限时'),'default duration is not unlimited');
byId('training-time-limit').value='90';byId('training-time-limit').dispatchEvent(new Event('input'));await settle();
check(byId('training-time-limit').value==='90','status refresh overwrote edited limit');
byId('training-time-save').click();await settle();
check(testSettingsCommands[0].time_limit_seconds===90 && byId('training-time-help').textContent.includes('90 秒'),'duration was not saved');
check(byId('training-live-message').textContent.includes('本轮不限时'),'saved setting altered current episode');
byId('training-time-limit').value='0';byId('training-time-limit').dispatchEvent(new Event('input'));byId('training-time-save').click();await settle();
check(testSettingsCommands.at(-1).time_limit_seconds===0,'unlimited setting did not persist');
check(testCommands.length===0 && testModelCommands.length===0,'duration setting sent a robot or model command');
''')

    def test_feedback_timeout_is_visible_during_policy_and_history_survives_recovery(self):
        self.browser(r'''
testState.runtime={autoserl_demo:true,phase:'connected'};
testState.ready=false;testState.reason='arm: stale stream';testState.pilot.mode='policy';
testState.telemetry.arm={ok:false,age:.612};await settle();
check(byId('status').textContent.includes('612 ms'),'timeout age missing or stale policy hides fault');
check(byId('error').textContent.includes('机械臂状态反馈超时'),'raw stale stream was not explained');
testState.health={last_fault:{time_unix:100,reason:'arm: stale stream',ages:{arm:.612}},recovered_unix:101};
testState.ready=true;testState.reason=null;testState.pilot.mode='locked';testState.telemetry.arm={ok:true,age:.01};
await settle();
check(byId('logs').textContent.includes('已保存反馈异常') && byId('logs').textContent.includes('反馈已恢复'),'transient fault disappeared');
check(byId('error-banner').hidden,'recovered feedback remains a current error');
check(testCommands.length===0,'diagnostics initiated robot actions');
''')

    def test_model_selection_and_preparation_never_start_robot(self):
        self.browser(r'''
testState.runtime={autoserl_demo:true,phase:'disconnected'};testState.ready=false;
testState.experiment={phase:'idle',active:false,can_prepare:true};await settle(1200);
check(!byId('training-workbench').hidden && !byId('model-prepare').disabled,'offline preparation unavailable');
check(testCommands.length===0 && testModelCommands.length===0,'opening training panel caused action');
byId('training-mode').value='resume';byId('training-mode').dispatchEvent(new Event('change'));
byId('model-checkpoint').value='early';byId('model-prepare').click();await settle();
check(testModelCommands[0].mode==='resume' && testModelCommands[0].model_id==='early','selected checkpoint was lost');
check(!testState.training.enabled && byId('training-run-toggle').disabled,'preparation started or enabled disconnected robot');
check(testCommands.length===0,'preparation sent a robot/start command');
byId('model-end').click();await settle();
byId('training-mode').value='fresh';byId('training-mode').dispatchEvent(new Event('change'));byId('model-prepare').click();await settle();
check(testModelCommands.at(-1).mode==='fresh' && testModelCommands.at(-1).model_id===null,'fresh preparation reused a model');
check(testCommands.length===0 && !testState.training.enabled,'fresh preparation started robot');
''')

    def test_training_dashboard_return_and_late_success_update(self):
        self.browser(r'''
testState.runtime={autoserl_demo:true,phase:'connected'};
testState.experiment={phase:'ready',active:true,managed:true,can_prepare:false,run_id:'run1',mode:'resume',model_label:'early'};
testState.training={available:true,session_id:'live',episode:0,phase:'awaiting_reset',enabled:true,can_label:true,steps:95,gradient_updates:4677,episode_return:0,message:'等待标记'};
await settle(1200);
check(byId('training-live-return').textContent==='0','live return missing');
check(byId('training-mean-return').textContent==='0.00' && byId('chart-return').querySelector('circle'),'history chart missing');
testTrainingData.summary.successes=1;testTrainingData.summary.last10_successes=1;testTrainingData.summary.mean_return=1;
Object.assign(testTrainingData.episodes[0],{outcome:'success',return_:1,success_last10:1});
testState.training.episode_return=1;testState.training.label='success';await settle(2400);
check(byId('training-live-return').textContent==='1' && byId('training-mean-return').textContent==='1.00','late success did not update returns');
check(byId('training-episodes').textContent.includes('成功') && byId('training-episodes').textContent.includes('力 / 力矩保护'),'label incorrectly inferred from force stop');
check(testCommands.length===0 && testModelCommands.length===0,'data analysis caused motion');
check(document.documentElement.scrollWidth<=innerWidth,'training dashboard overflows mobile');
''',width=390)

    def test_unassisted_evaluation_selects_model_and_episode_count_without_motion(self):
        self.browser(r'''
testState.runtime={autoserl_demo:true,phase:'connected'};
testState.experiment={phase:'idle',active:false,can_prepare:true};await settle(1200);
byId('training-mode').value='evaluate_unassisted';byId('training-mode').dispatchEvent(new Event('change'));
check(!byId('evaluation-settings').hidden && !byId('model-selection').hidden,'evaluation controls missing');
check(byId('training-mode-help').textContent.includes('关闭示范引导'),'independent mode not explained');
byId('model-checkpoint').value='latest';byId('evaluation-episodes').value='10';byId('model-prepare').click();await settle();
check(testModelCommands[0].mode==='evaluate_unassisted' && testModelCommands[0].model_id==='latest' && testModelCommands[0].episodes===10,'wrong evaluation mode, model or count');
check(!testState.training.enabled && testCommands.length===0,'preparation started robot');
''')

    def test_continuous_labels_after_stop_and_pause_are_available(self):
        self.browser(r'''
testState.pilot.online={available:true,phase:'ended',completed_index:119};
testState.training={available:true,session_id:'resident',episode:4,phase:'awaiting_reset',can_label:true,enabled:true,message:'回 Home 自动下一轮'};
await settle();
check(!byId('continuous-controls').hidden && byId('online-start').hidden,'resident controls missing');
check(!byId('finish-success').disabled && !byId('online-success').disabled,'late label unavailable');
byId('finish-success').click();await settle();
check(testCommands[0].action==='success' && testCommands[0].episode===4,'label lost or attached to wrong episode');
byId('online-toggle').click();await settle();
check(testCommands[1].action==='pause' && byId('online-toggle').textContent.includes('继续'),'pause not shown');
testState.training.available=false;await settle();
check(byId('continuous-controls').hidden && byId('online-success').disabled,'stale trainer still accepts labels');
testState.training={available:true,mode:'evaluation',phase:'completed',enabled:false,can_label:false,message:'冻结评估完成：A 3/5，B 1/5；已停止'};await settle();
check(!byId('continuous-controls').hidden && byId('continuous-status').textContent.includes('A 3/5'),'completed evaluation result missing');
check(byId('online-toggle').disabled && byId('online-success').disabled && byId('online-start').hidden,'completed evaluation can restart or relabel');
''')

    def test_unlabelled_stop_shows_pending_without_failure_or_zero_return(self):
        self.browser(r'''
testState.runtime={autoserl_demo:true,phase:'connected'};
testState.experiment={phase:'ready',active:true,managed:true,can_prepare:false,run_id:'run1',mode:'fresh'};
testState.pilot.online={available:true,phase:'ended',completed_index:94};
testState.training={available:true,session_id:'pending',episode:0,phase:'awaiting_label',enabled:true,can_label:true,steps:95,episode_return:null,label:null,message:'等待人工标记'};
Object.assign(testTrainingData.summary,{episodes:0,pending_episodes:1,last10_count:0,mean_return:null});
Object.assign(testTrainingData.episodes[0],{outcome:'pending',return_:null,valid:false,pending_label:true});
await settle(1200);
check(byId('training-live-return').textContent==='—' && byId('training-mean-return').textContent==='—','pending return reported as zero');
check(byId('continuous-status').textContent.includes('待标记') && !byId('continuous-status').textContent.includes('默认失败'),'pending stop reported as failure');
check(byId('training-episodes').textContent.includes('待标记') && !byId('training-episodes').textContent.includes('失败'),'pending history reported as failed');
check(!byId('chart-return').querySelector('circle'),'unlabelled return added to chart');
check(!byId('training-mark-success').disabled && !byId('training-mark-failure').disabled,'pending label buttons unavailable');
check(testCommands.length===0,'pending display sent robot commands');
''')

    def test_online_mode_keeps_stop_and_blocks_conflicting_controls(self):
        self.browser(r'''
testState.runtime={autoserl_demo:true,phase:'connected'};
testState.pilot.online={available:true,phase:'active',index:12,completed_index:11};
testState.pilot.mode='policy';await settle();
check(!byId('online-panel').hidden,'online panel missing');
check(byId('status').textContent.includes('策略'),'online status missing');
check(byId('home').disabled && byId('start').disabled && byId('gripper-open').disabled,'conflicting motion allowed');
check(!byId('finish').disabled,'online Stop unavailable');
byId('finish').click();await settle();
check(testCommands[0]==='finish' && current.pilot.mode==='locked','online Stop did not reach gate');
''')

    def test_recovery_check_starts_no_episode_and_keeps_stop_available(self):
        self.browser(r'''
check(byId('recovery-panel').hidden,'unconfigured probe offered');
testState.runtime={autoserl_demo:true,phase:'connected'};
testState.pilot.recovery_check={available:true,phase:'idle',point0:55,point1:103};
testState.pilot.online={available:true,phase:'idle',completed_index:-1};
testState.training={available:true,phase:'paused',enabled:false};
await settle();
check(!byId('recovery-panel').hidden && byId('recovery-check').disabled,'probe unavailable or permits a live actor');
byId('recovery-check').click();await settle();
check(testCommands.length===0,'disabled probe issued robot command');
testState.training.available=false;
testState.experiment={active:true,phase:'starting'};
await settle();
check(byId('recovery-check').disabled,'starting actor permits probe');
testState.experiment={active:false,phase:'ended'};
await settle();
check(!byId('recovery-check').disabled && !byId('recovery-panel').hidden,'configured probe missing');
byId('recovery-check').click();byId('recovery-check').click();await settle();
check(testCommands.length===1 && testCommands[0]==='recovery_check','duplicate or wrong probe command');
check(!current.recording && current.episodes.length===0,'probe created an expert episode');
check(byId('home').disabled && byId('start').disabled && byId('gripper-open').disabled,'probe allows conflicting motion');
check(!byId('finish').disabled,'probe cannot be stopped');
check(byId('status').textContent.includes('接近孔口'),'probe progress missing');
testState.pilot.recovery_check.phase='retreat_reference';
testState.pilot.recovery_check.retreat_requirements={position_tolerance_m:.002,settle_seconds:.3};
testState.pilot.recovery_check.retreat_progress={errors:[.0036,.01],linear_speed_m_s:.007,stable_seconds:0};
await settle();
check(byId('recovery-rule').textContent.includes('2 mm'),'retreat requirement missing');
check(byId('recovery-status').textContent.includes('3.60 mm') && byId('recovery-status').textContent.includes('7.0 mm/s'),'live retreat progress missing');
check(!byId('finish').disabled && byId('recovery-check').disabled,'settling hides Stop or permits repeated start');
byId('finish').click();await settle();
check(current.pilot.mode==='locked' && testCommands[1]==='finish','Stop failed');
''')

    def test_prepare_gripper_does_not_record_and_blocks_start_until_feedback(self):
        self.browser(r'''
check(testCommands.length===0,'loading page moved gripper');
check(!byId('gripper-open').disabled && !byId('gripper-close').disabled,'preparation unavailable');
testState.telemetry.gripper.preparation_supported=false;await settle();
check(byId('gripper-open').disabled && byId('gripper-status').textContent.includes('重新连接'),'old control session accepted new command');
testState.telemetry.gripper.preparation_supported=true;await settle();
testDelay=1000;
byId('gripper-close').click();byId('gripper-close').click();await settle(100);
check(testCommands.length===1,'duplicate gripper command');
check(byId('start').disabled && byId('home').disabled,'arm motion allowed during preparation');
check(!current.recording && current.episodes.length===0,'preparation created episode');
check(!byId('finish').disabled,'Stop unavailable during preparation');
await settle(1500);
check(!byId('start').disabled && !current.recording,'preparation failed to reconcile');
check(byId('gripper-status').textContent.includes('接触物体'),'contact feedback not shown');
check(byId('command-feedback').textContent.includes('未录制'),'no recording status missing');
testDelay=0;byId('gripper-open').click();await settle();
check(current.telemetry.gripper_position===0 && current.episodes.length===0,'open started recording');
byId('start').click();await settle();
check(byId('gripper-open').disabled && byId('gripper-close').disabled,'idle preparation enabled during capture');
byId('finish').click();await settle();byId('home').click();await settle();
check(byId('gripper-open').disabled,'gripper enabled during Home');
byId('finish').click();await settle();testOffline=true;await settle();
check(byId('gripper-open').disabled && byId('gripper-close').disabled,'offline commands available');
''')

    def test_demo_gripper_stop_and_start_preserve_grasp_without_device_requests(self):
        self.browser(r'''
const count=current.episodes.length;
byId('gripper-close').click();byId('finish').click();await settle(1200);
check(!current.recording && current.episodes.length===count,'Stop invented an episode');
byId('gripper-close').click();await settle(1200);
const position=current.telemetry.gripper_position;
byId('start').click();await settle(1200);
check(current.telemetry.gripper_position===position,'Start changed the grasp');
check(current.recording,'Start unavailable after preparation');
check(testCommands.length===0,'demo contacted a device');
''',demo=True,width=390)

    def browser(self, exercise, *, demo=False, width=1440, scene='idle', screenshot=None):
        page=(ROOT/'pilot_capture.html').read_text()
        page=re.sub(r'<script defer src="[^"]+"></script>', '', page)
        page=page.replace('<link rel="stylesheet" href="/ui/theme.css">', '<style>'+(ROOT/'pilot_ui/theme.css').read_text()+'</style>')
        scripts='\n'.join((ROOT/'pilot_ui'/name).read_text() for name in ('api.js','demo.js','cameras.js','catalog.js','records.js','training.js','app.js'))
        report=r'''
const settle=(ms=650)=>new Promise(resolve=>setTimeout(resolve,ms));
const check=(condition,message)=>{if(!condition)throw Error(message);};
const report=data=>{const node=document.createElement('pre');node.id='browser-test-result';node.hidden=true;node.textContent=JSON.stringify(data);document.body.append(node);};
(async()=>{try{await settle();EXERCISE;report({ok:true,errors:testErrors,width:innerWidth});}catch(error){report({ok:false,message:String(error),errors:testErrors,commands:testCommands});}})();
'''.replace('EXERCISE',exercise)
        page=page.replace('</body>','<script>'+BOOTSTRAP+'\n'+scripts+'\n'+report+'</script></body>')
        with tempfile.TemporaryDirectory(prefix='hilserl-ui-test-') as directory:
            root=Path(directory);path=root/'fixture.html';path.write_text(page)
            url=path.as_uri()+('?demo=1&scene='+scene if demo else '')
            args=[shutil.which('google-chrome'),'--headless','--disable-gpu','--disable-dev-shm-usage',
                '--no-first-run','--no-default-browser-check','--disable-background-networking',
                '--host-resolver-rules=MAP * ~NOTFOUND','--user-data-dir='+str(root/'profile'),
                '--window-size=%d,1000'%width,'--force-device-scale-factor=1',
                '--virtual-time-budget=11000','--dump-dom']
            if screenshot:
                args.append('--screenshot='+str(screenshot))
            result=subprocess.run(args+[url],capture_output=True,text=True,timeout=30)
            match=re.search(r'<pre id="browser-test-result" hidden="">(.*?)</pre>',result.stdout,re.S)
            self.assertIsNotNone(match,result.stderr[-1500:]+result.stdout[-1500:])
            data=json.loads(unescape(match.group(1)))
            self.assertTrue(data['ok'],data)
            self.assertEqual(data['errors'],[],data)
            return data

    def test_real_buttons_keep_control_semantics_and_user_selection(self):
        self.browser(r'''
check(testCommands.length===0,'page load sent a command');
check(!byId('home').disabled && !byId('start').disabled,'idle actions disabled');
byId('home').click(); await settle();
check(byId('start').disabled,'Start enabled during Home');
check(byId('status').textContent.includes('返回 DROID Home'),'Home phase missing');
byId('finish').click(); await settle();
byId('start').click(); await settle();
byId('finish-success').click(); await settle();
check(JSON.stringify(testCommands)===JSON.stringify(['home','finish','start','finish_success']),'wrong command sequence');
check(current.episodes[0].outcome==='success','outcome not saved');
check(byId('success-rate').textContent==='100%','success denominator incorrect');
check(byId('set_home').hidden,'official target overwrite offered');
byId('episode-filter').value='failure';byId('episode-filter').dispatchEvent(new Event('change'));
check(!byId('empty-records').hidden,'filtered empty state missing');
''')

    def test_pending_commands_block_duplicates_and_finish_preempts_home(self):
        self.browser(r'''
testDelay=1100;
byId('home').click();byId('home').click(); await settle(100);
check(testCommands.length===1,'duplicate Home accepted');
check(byId('start').disabled,'Start enabled before ack');
check(!byId('finish').disabled,'Finish blocked by Home request');
byId('finish').click();byId('top-finish').click();await settle(1900);
check(JSON.stringify(testCommands)===JSON.stringify(['home','finish']),'Finish duplicate or missing');
check(current.pilot.mode==='locked','Finish did not hold');
check(!byId('start').disabled,'state did not reconcile');
''')

    def test_success_fail_stop_save_once_and_stop_can_be_reviewed_later(self):
        self.browser(r'''
check(!byId('outcome'),'obsolete result selector remains');
for(const [button,outcome] of [['finish-success','success'],['finish-failure','failure'],['finish','unlabeled']]){
 check(byId('finish-success').disabled && byId('finish-failure').disabled,'saved record can be mislabeled by capture controls');
 byId('start').click();await settle(1200);
 const count=current.episodes.length;
 byId(button).click();byId(button).click();byId('top-finish').click();await settle(1200);
 check(!current.recording && current.episodes.length===count+1,'duplicate or missing save');
 check(current.episodes.at(-1).outcome===outcome,'wrong saved label for '+button);
 check(byId('episodes').firstElementChild.cells[5].textContent.includes(outcomeLabels[outcome]),'table label stale');
}
check(new Set(current.episodes.map(e=>e.name)).size===current.episodes.length,'duplicate episode names');
byId('episodes').firstElementChild.querySelector('button').click();
byId('record-outcome').value='success';byId('save-review').click();await settle();
check(current.episodes.at(-1).outcome==='success','Stop result cannot be reviewed');
check(byId('episode-detail').textContent.includes('成功'),'detail retains stale label');
check(testCommands.length===0,'demo reached robot');
''',demo=True)

    def test_records_show_their_own_tasks_and_unique_legacy_session_names(self):
        self.browser(r'''
testState.task={name:'currently selected unrelated task'};
testState.episodes=[
 {id:'a',name:'episode_0001',session:'pilot_A',task:'insertion',task_display_name:'插入工具',task_id:'task_a',outcome:'success',samples:10},
 {id:'b',name:'episode_0001',session:'pilot_B',task:'stacking',task_display_name:'堆叠',task_id:'task_b',outcome:'unlabeled',samples:10},
 {id:'c',name:'episode_0001',session:'pilot_C',outcome:'unlabeled',samples:10}
];await settle();
const rows=[...byId('episodes').rows];
check(rows.map(r=>r.cells[0].textContent).join('|')==='未记录任务|堆叠|插入工具','record task borrowed from current selection');
check(new Set(rows.map(r=>r.cells[1].textContent)).size===3,'legacy names still collide');
byId('record-task-filter').value='task_b';byId('record-task-filter').dispatchEvent(new Event('change'));
check(byId('episodes').rows.length===1 && byId('episodes').rows[0].cells[0].textContent==='堆叠','task filter incorrect');
check(testCommands.length===0,'record display issued robot command');
''',width=390)

    def test_demo_custom_home_is_independent_and_never_reaches_robot(self):
        root=os.environ.get('PILOT_SCREENSHOT_DIR')
        if root:Path(root).mkdir(parents=True,exist_ok=True)
        self.browser(r'''
const droid=JSON.stringify(current.pilot.home);
byId('set-custom-home').click();await settle(1200);
check(current.pilot.custom_home.available,'demo teach failed');
byId('custom-home').click();await settle(1200);
check(current.pilot.active_home==='custom' && current.pilot.mode==='homing','demo wrong home');
byId('finish').click();await settle(1200);
check(current.pilot.mode==='locked','demo Finish failed');
check(JSON.stringify(current.pilot.home)===droid,'demo modified DROID');
check(testCommands.length===0,'demo reached device commands');
document.querySelector('.custom-home-panel details').open=true;
document.querySelector('.home-panel').scrollIntoView({block:'center',behavior:'instant'});await settle(100);
check(byId('set-custom-home').getBoundingClientRect().bottom<innerHeight,'custom Home button is clipped');
''',demo=True,screenshot=Path(root)/'custom-home-panel.png' if root else None)

    def test_independent_joint_home_teach_overwrite_return_and_stop(self):
        self.browser(r'''
const droid=JSON.stringify(current.pilot.home);
check(byId('custom-home').disabled && !byId('set-custom-home').disabled,'unset custom target permits return');
byId('set-custom-home').click();await settle();
check(current.pilot.custom_home.available && !byId('custom-home').disabled,'teach failed');
check(JSON.stringify(current.pilot.home)===droid,'custom teach overwrote DROID');
check(current.pilot.mode==='locked','teach moved robot');
byId('set-custom-home').click();check(byId('confirm-home').open,'overwrite lacks confirmation');
check(byId('confirm-home-message').textContent.includes('DROID Home 保持固定'),'confirmation confuses targets');
byId('confirm-home').close('cancel');await settle();check(testCommands.length===1,'cancel changed target');
byId('custom-home').click();await settle();
check(byId('status').textContent.includes('自定义 Home'),'wrong homing target label');
check(byId('home').disabled && byId('start').disabled && byId('set-custom-home').disabled,'motion/teach during Home permitted');
byId('finish').click();await settle();
byId('home').click();await settle();
check(byId('status').textContent.includes('DROID Home'),'DROID button reused custom target');
byId('finish').click();await settle();
check(JSON.stringify(testCommands)===JSON.stringify(['set_custom_home','custom_home','finish','home','finish']),'targets share command');
testState.pilot.custom_home.saving=true;await settle();
check(byId('start').disabled && byId('home').disabled && byId('custom-home').disabled && !byId('finish').disabled,'save interlock incorrect');
testState.pilot.custom_home.saving=false;testOffline=true;await settle();
check(byId('custom-home').disabled && byId('set-custom-home').disabled,'offline custom actions enabled');
''')

    def test_web_connection_is_not_reported_as_a_robot_connection(self):
        self.browser(r'''
testState.ready=false;testState.pilot={};testState.reason='控制会话未连接';
testState.runtime={phase:'disconnected',can_connect:true,can_disconnect:false,home_source:'droid_default',home_joint_degrees:[0,-36,0,-144,0,108,0]};
await settle();
check(document.body.dataset.state==='disconnected','idle control connection shown as loading');
check(byId('connection').textContent.includes('网页在线'),'web and robot connection conflated');
check(byId('start').disabled && byId('home').disabled,'unconnected control permits motion');
check(byId('home_status').textContent.includes('DROID') && byId('home-target').textContent.includes('-144'),'configured Home is missing');
check(byId('error-banner').hidden,'normal disconnected state reported as an error');
testState.runtime.phase='error';testState.runtime.error='Robot unreachable';await settle();
check(!byId('error-banner').hidden && byId('error').textContent.includes('Robot unreachable'),'runtime failure hidden');
check(testCommands.length===0,'status rendering sent motion');
''')

    def test_offline_error_recovery_custom_home_and_unlabeled_stop(self):
        self.browser(r'''
testOffline=true;await settle();
check(byId('start').disabled && byId('home').disabled,'offline motion allowed');
check(byId('service-motion').textContent.includes('未知'),'stale motion looks live');
check(!byId('finish').disabled,'offline stop is inaccessible');
testOffline=false;testState.pilot.home_kind='cartesian';testState.pilot.home={xyz:[.3,0,.4]};await settle();
check(!byId('start').disabled,'reconnect failed');
byId('home-details').open=true;byId('set_home').click();
check(byId('confirm-home').open,'overwrite confirmation absent');
byId('confirm-home').close('cancel');await settle();check(testCommands.length===0,'cancel changed Home');
byId('set_home').click();byId('confirm-set-home').click();await settle();
check(testCommands[0]==='set_home','confirmed Home not sent');
testReject=true;byId('start').click();await settle();
check(byId('error').textContent.includes('controller rejected command'),'HTTP error not visible');
testReject=false;byId('start').click();await settle();byId('finish').click();await settle();
check(current.episodes[0].outcome==='unlabeled','plain stop automatically labeled result');
check(byId('success-rate').textContent==='—','unlabeled counted as failure');
''')

    def test_camera_error_is_visible_logged_and_recovers_without_motion(self):
        self.browser(r'''
testState.ready=false;testState.pilot={};testState.runtime={phase:'disconnected',can_connect:true};
testState.camera_ages={external:null,wrist:null};
testState.camera_errors={external:'Camera permission denied (read/write): /dev/v4l/by-path/pci-0000:00:14.0-usb-0:6.1:1.0-video-index0',wrist:'Camera device not found'};
await settle();
check(byId('external-badge').textContent.includes('异常'),'permission failure looks like loading');
check(byId('external-placeholder').textContent.includes('permission denied'),'camera error missing from view');
check(!byId('error-banner').hidden && byId('logs').textContent.includes('permission denied'),'camera error not in banner and log');
check(document.documentElement.scrollWidth<=innerWidth,'long camera path breaks layout');
testOffline=true;await settle();
check(byId('external-placeholder').textContent.includes('过期'),'stale camera state not marked');
testOffline=false;testState.camera_errors={external:null,wrist:null};await settle();
check(byId('external-badge').textContent.includes('连接中'),'camera retry did not clear failure');
check(byId('error-banner').hidden,'old camera error retained after recovery');
check(testCommands.length===0,'camera recovery sent motion command');
''',width=390)

    def test_demo_states_are_isolated_and_recover(self):
        self.browser(r'''
for(const scene of ['loading','running','saving','homing','error','offline','empty','idle']){
 byId('demo-state').value=scene;byId('demo-state').dispatchEvent(new Event('change'));await settle();
 check(!byId('demo-banner').hidden,'demo mode not labeled');
 if(['homing','saving','running','offline','error','loading'].includes(scene))check(byId('start').disabled,'invalid Start in '+scene);
 if(scene==='offline')check(document.body.dataset.state==='offline','offline not shown');
 if(scene==='error')check(!byId('error-banner').hidden,'error not shown');
 if(scene==='empty')check(!byId('empty-records').hidden,'empty records missing');
}
byId('start').click();await settle(1200);byId('finish').click();await settle(1200);
check(testCommands.length===0,'demo reached live command API');
check(current.episodes.at(-1).outcome==='unlabeled','demo finish not saved');
''',demo=True)

    def test_task_creation_prompt_edit_layout_and_record_snapshot(self):
        self.browser(r'''
const original=current.task.id;
byId('new-task').click();byId('catalog-name').value='新的插入任务';byId('catalog-prompt').value='Insert the tool';
byId('catalog-form').requestSubmit();await settle();
check(current.task.name==='新的插入任务','new task not selected');
byId('prompt-input').value='Insert the tool carefully';byId('prompt-input').dispatchEvent(new Event('input'));
await settle();check(byId('start').disabled,'dirty prompt allows capture');
check(byId('prompt-input').value==='Insert the tool carefully','polling overwrote edited prompt');
byId('apply-prompt').click();await settle();
check(current.task.prompt==='Insert the tool carefully','task default prompt not updated');
byId('capture-layout').click();byId('catalog-name').value='偏置摆放';byId('catalog-ood').checked=true;
byId('catalog-form').requestSubmit();await settle();
check(current.layout.ood && current.layout.name==='偏置摆放','layout not saved');
byId('ghost-enabled').checked=true;byId('ghost-enabled').dispatchEvent(new Event('input'));
check(!byId('layout-ghost').hidden,'ghost missing');
byId('start').click();await settle(1200);check(byId('task-select').disabled,'task can change during capture');
byId('finish').click();await settle(1200);
const episode=current.episodes.at(-1);check(episode.prompt==='Insert the tool carefully' && episode.layout.ood,'record lacks configuration snapshot');
byId('task-select').value=original;byId('task-select').dispatchEvent(new Event('change'));await settle();
check(current.task.id===original && current.layout===null,'task switching retains foreign layout');
check(testCommands.length===0,'demo contacted real control');
''',demo=True)

    def test_unacknowledged_command_times_out_without_replay_and_logs_keep_position(self):
        self.browser(r'''
testDelay=20000;
byId('start').click();await settle(7200);
check(testCommands.length===1,'unacknowledged command was replayed');
check(byId('error').textContent.includes('未在预期时间内确认'),'ack timeout missing');
check(!byId('finish').disabled,'stop unavailable after timeout');
activateTab('logs');
for(let i=0;i<60;i++)log('test event '+i);
byId('logs').scrollTop=0;byId('logs').dispatchEvent(new Event('scroll'));
log('must not pull reader to bottom');
check(byId('logs').scrollTop===0,'log view interrupted manual reading');
''')

    def test_saved_record_review_and_playback_never_issue_motion(self):
        self.browser(r'''
const entry=current.episodes.find(e=>e.outcome==='unlabeled') || current.episodes[0];
showEpisode(entry);byId('record-notes').value='Reviewed after capture';byId('record-outcome').value='success';
await settle();check(byId('record-notes').value==='Reviewed after capture','polling overwrote notes');
byId('load-recording').click();await settle();check(!byId('episode-playback').hidden,'playback did not load');
byId('save-review').click();await settle();
check(current.episodes.find(e=>e.name===entry.name).notes==='Reviewed after capture','review not saved');
byId('delete-record').click();check(!byId('confirm-record-delete').hidden,'deletion lacks confirmation');
byId('accept-record-delete').click();await settle();
check(!current.episodes.some(e=>e.name===entry.name),'record not removed');
check(testCommands.length===0,'record review caused motion');
''',demo=True)

    def test_layouts_have_no_page_overflow(self):
        screenshot_root=os.environ.get('PILOT_SCREENSHOT_DIR')
        if screenshot_root: Path(screenshot_root).mkdir(parents=True,exist_ok=True)
        for width in (1920,1440,1024,390):
            with self.subTest(width=width):
                screenshot=Path(screenshot_root)/('capture-%d.png'%width) if screenshot_root else None
                data=self.browser(r'''
check(document.documentElement.scrollWidth<=innerWidth,'page horizontally overflows');
check(byId('start').getBoundingClientRect().width>100,'Start too narrow');
check(byId('top-finish').getBoundingClientRect().right<=innerWidth,'sticky stop outside viewport');
if(innerWidth>=1024){check(byId('finish').getBoundingClientRect().bottom<900,'primary actions below desktop fold');check(document.querySelector('.records-panel').getBoundingClientRect().top<900,'records below desktop fold');}
check(testCommands.length===0,'preview sent a device command');
''',demo=True,width=width,screenshot=screenshot)
                # New Chrome may enforce a 500px minimum window. Document the actual viewport.
                self.assertLessEqual(data['width'],max(width,500))
