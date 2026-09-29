'use strict';
const PilotTrainingUI = (() => {
  let catalog={runs:[]}, loaded=false, loading=false, pending=false, state={}, online=false;
  let metrics=null, metricBusy=false, lastMetricAt=0, chosenRun='', requestId=0;
  let limitBusy=false,limitDirty=false;
  const labels={fresh:'从头训练',resume:'从模型续训',evaluate:'有自动干预评估',evaluate_unassisted:'无自动干预评估'};
  const reasonLabels={force_limit:'力 / 力矩保护',success:'成功按钮',operator_abort:'人工失败',spacemouse_takeover:'SpaceMouse 接管',time_limit:'回合上限'};
  const set=(id,text)=>{document.getElementById(id).textContent=text;};
  const get=id=>document.getElementById(id);
  const fmt=(value,digits=2)=>value==null?'—':Number(value).toFixed(digits);
  const pct=value=>value==null?'—':(100*value).toFixed(1)+'%';
  function options(element,rows,placeholder){
    const before=element.value;
    element.replaceChildren(...rows.map(row=>new Option(row.label,row.id)));
    if(!rows.length)element.add(new Option(placeholder,''));
    if(rows.some(row=>row.id===before))element.value=before;
  }
  const algorithm=()=>get('training-algorithm').value;
  const selectedRuns=()=>catalog.runs.filter(r=>(r.algorithm_id || 'autoserl')===algorithm());
  function checkpoints(){
    const run=catalog.runs.find(r=>r.id===get('model-run').value);
    options(get('model-checkpoint'),(run?.checkpoints || []).map(m=>({id:m.id,label:`更新 ${m.updates.toLocaleString()} · ${new Date(m.saved_unix*1000).toLocaleString()}`})),'暂无可选模型');
    controls();
  }
  function controls(){
    const hil=algorithm()==='hilserl',job=state.experiment || {};
    const assisted=get('training-mode').querySelector('[value="evaluate"]');assisted.disabled=hil;assisted.hidden=hil;
    if(hil && get('training-mode').value==='evaluate')get('training-mode').value='evaluate_unassisted';
    const mode=get('training-mode').value;
    get('model-selection').hidden=mode==='fresh';get('evaluation-settings').hidden=!['evaluate','evaluate_unassisted'].includes(mode);
    set('training-mode-help',({fresh:'沿用当前 1 条 demo，初始化新模型和新训练记录。旧记录保留。',resume:'加载所选模型及其保存时已有的数据，开启新一段训练；自动干预重新启用。',evaluate:'固定所选模型参数，按指定轮数评估；保留自动干预，结果单独保存。',evaluate_unassisted:'冻结所选模型，关闭示范引导和自动回退重放，测试策略独立表现。仍需人工标记成功 / 失败并复位。'})[mode]);
    if(hil)set('training-mode-help',mode==='evaluate_unassisted'?'冻结 HIL-SERL 模型，关闭人工接管和自动恢复，测试独立成功率。':mode==='resume'?'恢复 HIL-SERL 模型和当时的数据；SpaceMouse 随时可接管。':'当前 1 条 demo 初始化独立 HIL-SERL 模型；移动 SpaceMouse 接管，回中交还策略。不启用自动恢复。');
    for(const id of ['training-algorithm','training-mode','model-run','model-checkpoint','evaluation-episodes'])get(id).disabled=pending || !!job.active;
    const selectionOK=mode==='fresh' || !!get('model-checkpoint').value;
    get('model-prepare').disabled=!online || !loaded || pending || job.can_prepare!==true || !selectionOK || !!state.recording;
    get('model-end').disabled=!online || pending || !job.managed || job.phase==='stopping';
    set('model-job-state',({idle:'未启动',loading:'加载模型中',ready:'已准备',stopping:'保存并退出中',ended:'已结束',error:'启动失败',external:'其他训练进程运行中'})[job.phase] || '等待网页状态');
    set('model-job-detail',job.model_label?`${(job.algorithm_id || 'autoserl').toUpperCase()} · ${labels[job.mode] || ''} · ${job.model_label}`:'');
    if(job.error)set('training-error',job.error);
  }
  async function loadCatalog(){
    if(loading || PilotAPI.isDemo)return;
    loading=true;
    try{
      catalog=await PilotAPI.models();loaded=true;set('training-error','');
      options(get('model-run'),selectedRuns().filter(r=>r.checkpoints.length).map(r=>({id:r.id,label:r.name})),'暂无兼容的训练模型');
      const previousHistory=get('training-history').value;
      options(get('training-history'),selectedRuns().map(r=>({id:r.id,label:`${r.kind==='evaluation'?'评估':'训练'} · ${r.name}`})),'暂无训练记录');
      if(!previousHistory){const recent=selectedRuns().find(r=>r.kind==='training' && r.checkpoints.length);if(recent)get('training-history').value=recent.id;}
      set('model-demo',`初始示范：${catalog.initial_demo_episodes} 条 · ${catalog.demo_name}`);
      checkpoints();follow();await loadMetrics(true);
    }catch(error){set('training-error','模型列表读取失败：'+error.message);}
    finally{loading=false;controls();}
  }
  function follow(){
    const rid=state.experiment?.run_id;
    if(get('training-follow').checked && rid && selectedRuns().some(r=>r.id===rid))get('training-history').value=rid;
  }
  function svgElement(name,attrs={},text=null){
    const el=document.createElementNS('http://www.w3.org/2000/svg',name);
    for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);
    if(text!=null)el.textContent=text;return el;
  }
  function chart(id,points,{range=null,percentage=false}={}){
    const svg=get(id);svg.replaceChildren();
    points=points.filter(p=>Number.isFinite(p[0]) && Number.isFinite(p[1]));
    if(!points.length){svg.append(svgElement('text',{x:260,y:90,'text-anchor':'middle',class:'chart-label'},'暂无数据'));return;}
    let x0=points[0][0],x1=points.at(-1)[0];if(x0===x1)x1=x0+1;
    let y0=range?.[0] ?? Math.min(0,...points.map(p=>p[1])),y1=range?.[1] ?? Math.max(...points.map(p=>p[1]));
    if(y0===y1)y1=y0+1;
    const x=v=>48+(v-x0)/(x1-x0)*452,y=v=>151-(v-y0)/(y1-y0)*133;
    for(let i=0;i<3;i++){
      const value=y0+(y1-y0)*i/2;
      svg.append(svgElement('line',{x1:48,x2:500,y1:y(value),y2:y(value),class:'chart-grid'}));
      svg.append(svgElement('text',{x:42,y:y(value)+4,'text-anchor':'end',class:'chart-label'},percentage?Math.round(value*100)+'%':Number(value.toPrecision(3))));
    }
    svg.append(svgElement('polyline',{points:points.map(p=>`${x(p[0])},${y(p[1])}`).join(' '),class:'chart-line'}));
    for(const p of points){const dot=svgElement('circle',{cx:x(p[0]),cy:y(p[1]),r:3,class:'chart-dot'});dot.append(svgElement('title',{},`${p[0]} · ${percentage?pct(p[1]):fmt(p[1],4)}`));svg.append(dot);}
    svg.append(svgElement('text',{x:48,y:175,class:'chart-label'},points[0][0]),svgElement('text',{x:500,y:175,'text-anchor':'end',class:'chart-label'},points.at(-1)[0]));
  }
  function showMetrics(data){
    const s=data.summary;
    set('training-success-total',`${s.successes} / ${s.episodes}`);
    set('training-success-recent',`${s.last10_successes} / ${s.last10_count}`);
    const hil=data.algorithm_id==='hilserl';
    set('training-intervention-label',hil?'人工干预动作':'自动干预动作');set('training-intervention-column',hil?'人工干预':'自动干预');
    set('training-mean-return',fmt(s.mean_return));set('training-auto-fraction',pct(hil?s.human_fraction:s.automatic_fraction));
    const selected=catalog.runs.find(r=>r.id===data.run_id);
    set('training-data-note',`${selected?.kind==='evaluation'?'冻结评估':'在线训练'} · ${s.transitions.toLocaleString()} 步 · ${s.pending_episodes || 0} 轮待标记 · ${s.excluded_episodes} 轮中断 / 排除。待标记回合不计成功率和平均 Return。最多显示最近 1000 轮。`);
    const valid=data.episodes.filter(e=>e.valid);
    chart('chart-return',valid.map(e=>[e.episode,e.return_]),{range:[0,1]});
    chart('chart-success',valid.map(e=>[e.episode,e.success_last10]),{range:[0,1],percentage:true});
    chart('chart-q',data.learner.map(m=>[m.updates,m.q]));chart('chart-loss',data.learner.map(m=>[m.updates,m.critic_loss]));
    const last=data.learner.at(-1);
    set('learner-summary',last?`更新 ${last.updates.toLocaleString()} · 在线数据 ${last.online_steps ?? '—'} 步 · Actor Loss ${fmt(last.actor_loss,4)} · 策略熵 ${fmt(last.entropy,3)}`:'暂无学习日志；冻结评估不更新参数。');
    const rows=data.episodes.slice().reverse().map(e=>{
      const tr=document.createElement('tr'),count=hil?e.human_interventions:e.automatic_interventions;
      for(const text of [e.episode,e.pending_label?'待标记':e.valid?(e.outcome==='success'?'成功':'失败'):'排除',fmt(e.return_,0),e.steps,count==null?'—':`${count} (${pct(e.steps?count/e.steps:0)})`,reasonLabels[e.reason] || e.reason || '人工停止',e.pending_label?'等待人工标记':!e.valid?'中断，不计成功率':e.provisional?'可补标':'已保存']){const td=document.createElement('td');td.textContent=text;tr.append(td);}
      return tr;
    });
    get('training-episodes').replaceChildren(...rows);get('training-empty').hidden=!!rows.length;get('training-export').disabled=!rows.length;
    set('training-data-age','已更新 · '+new Date(data.generated_unix*1000).toLocaleTimeString());
  }
  async function loadMetrics(force=false){
    const rid=get('training-history').value;
    if(!rid || PilotAPI.isDemo || metricBusy || (!force && Date.now()-lastMetricAt<2000))return;
    metricBusy=true;lastMetricAt=Date.now();const id=++requestId;chosenRun=rid;
    try{const data=await PilotAPI.trainingData(rid);if(id===requestId && get('training-history').value===rid){metrics=data;showMetrics(data);}}
    catch(error){set('training-data-age','读取失败：'+error.message);}
    finally{metricBusy=false;}
  }
  async function jobAction(action){
    if(pending)return;pending=true;controls();set('training-error','');
    try{
      const mode=get('training-mode').value;
      await PilotAPI.experiment(action==='stop'?{action}:{action,mode,algorithm:algorithm(),model_id:mode==='fresh'?null:get('model-checkpoint').value,episodes:Number(get('evaluation-episodes').value)});
      set('model-job-detail',action==='prepare'?'正在加载模型；加载完成后保持暂停。':'正在保存并退出…');
      await refresh();
    }catch(error){set('training-error',error.message);}
    finally{pending=false;controls();}
  }
  function render(s,isOnline){
    state=s;online=isOnline;
    if(s.experiment?.active && s.experiment.algorithm_id && algorithm()!==s.experiment.algorithm_id){get('training-algorithm').value=s.experiment.algorithm_id;loaded=false;}
    const visible=!!s.runtime?.autoserl_demo || !!s.experiment;
    get('training-workbench').hidden=!visible;
    if(!visible || PilotAPI.isDemo)return;
    const savedLimit=s.training_settings?.time_limit_seconds ?? 0;
    if(!limitDirty && document.activeElement!==get('training-time-limit'))get('training-time-limit').value=savedLimit;
    get('training-time-save').disabled=!online || limitBusy;
    get('training-time-limit').disabled=limitBusy;
    if(!limitBusy)set('training-time-help',limitDirty?'尚未保存；保存后从下一轮生效。':`已保存：${savedLimit===0?'不限时':savedLimit+' 秒'}；从下一轮生效。`);
    if(!loaded && !loading)loadCatalog();
    const t=s.training || {},active=online && !!t.available && !['stopped','completed'].includes(t.phase);
    set('training-live-return',active?fmt(t.episode_return,0):'—');
    set('training-live-step',active?`${t.episode+1} / ${t.steps ?? 0}`:'—');
    set('training-live-updates',active?(t.frozen?'冻结 · 0':Number(t.gradient_updates || 0).toLocaleString()):'—');
    set('training-live-phase',active?(t.frozen?'冻结评估':'在线训练'):'未运行');
    set('training-live-model',s.experiment?.model_label || '');set('training-live-message',(t.message || '准备任务后在这里开始。')+(t.phase==='running'?` · 控制来源：${({human:'人工',automatic:'自动恢复',policy:'策略'})[t.intervention_source] || '策略'}`:'')+(t.phase==='running'?` · 本轮${t.time_limit_seconds?t.time_limit_seconds+' 秒上限':'不限时'}`:''));
    get('training-run-toggle').disabled=!active || (!t.enabled && !s.ready);
    set('training-run-toggle',t.enabled?'暂停运行':'开始运行');
    for(const id of ['training-mark-success','training-mark-failure'])get(id).disabled=!active || !t.can_label;
    if(loaded){
      follow();if(chosenRun!==get('training-history').value)lastMetricAt=0;
      loadMetrics();
      if(s.experiment?.run_id && s.experiment.phase==='ready' && !catalog.runs.some(r=>r.id===s.experiment.run_id) && !loading)loadCatalog();
    }
    controls();
  }
  function init(){
    get('training-time-limit').addEventListener('input',()=>{limitDirty=true;set('training-time-help','尚未保存；保存后从下一轮生效。');});
    get('training-time-save').addEventListener('click',async()=>{
      if(limitBusy)return;
      const raw=get('training-time-limit').value,seconds=Number(raw);
      if(raw==='' || !Number.isSafeInteger(seconds) || seconds<0){set('training-error','请输入非负整数秒；0 表示不限时。');return;}
      limitBusy=true;get('training-time-save').disabled=true;set('training-error','');
      try{await PilotAPI.trainingSettings({time_limit_seconds:seconds});limitDirty=false;await refresh();}
      catch(error){set('training-error',error.message);}
      finally{limitBusy=false;get('training-time-save').disabled=!online;}
    });
    get('training-algorithm').addEventListener('change',()=>{requestId++;metrics=null;chosenRun='';get('training-episodes').replaceChildren();get('training-export').disabled=true;for(const id of ['training-success-total','training-success-recent','training-mean-return','training-auto-fraction'])set(id,'—');for(const id of ['chart-return','chart-success','chart-q','chart-loss'])chart(id,[]);loadCatalog();controls();});
    get('models-refresh').addEventListener('click',loadCatalog);
    get('training-mode').addEventListener('change',controls);get('model-run').addEventListener('change',checkpoints);
    get('model-checkpoint').addEventListener('change',controls);
    get('model-prepare').addEventListener('click',()=>jobAction('prepare'));get('model-end').addEventListener('click',()=>jobAction('stop'));
    get('training-run-toggle').addEventListener('click',()=>sendTraining(state.training?.enabled?'pause':'resume'));
    get('training-mark-success').addEventListener('click',()=>sendTraining('success'));get('training-mark-failure').addEventListener('click',()=>sendTraining('failure'));
    get('training-history').addEventListener('change',()=>{get('training-follow').checked=false;lastMetricAt=0;loadMetrics(true);});
    get('training-follow').addEventListener('change',()=>{follow();loadMetrics(true);});
    get('training-export').addEventListener('click',()=>{
      if(!metrics)return;
      const rows=[['episode','outcome','return','steps','automatic_interventions','human_interventions','stop_reason','valid','provisional'],...metrics.episodes.map(e=>[e.episode,e.outcome,e.return_,e.steps,e.automatic_interventions,e.human_interventions,e.reason,e.valid,e.provisional])];
      const csv=rows.map(row=>row.map(v=>'"'+String(v??'').replaceAll('"','""')+'"').join(',')).join('\r\n');
      const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=metrics.name+'-episodes.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
  }
  return {init,render};
})();
