"use strict";
const PilotRewardUI=(()=>{
  const get=id=>document.getElementById(id),set=(id,value)=>{get(id).textContent=value;};
  let data=null,busy=false,loading=false,active=false,loadedAt=0,frameRequest=0,frameReady=false;
  function controls(){
    const disabled=busy || PilotAPI.isDemo;
    for(const id of ['reward-import','reward-positive','reward-negative','reward-skip','reward-capture-positive','reward-capture-negative'])get(id).disabled=disabled;
    for(const id of ['reward-positive','reward-negative','reward-skip'])get(id).disabled=disabled || !frameReady;
    get('reward-train').disabled=disabled || active || !!data?.running || !data?.can_train;
    get('reward-activate').disabled=disabled || active || !get('reward-model').value;
  }
  function options(id,rows){
    const element=get(id),previous=element.value;element.replaceChildren(...rows.map(r=>new Option(r.name || r.id,r.id)));
    if(!rows.length)element.add(new Option('暂无',''));
    if(rows.some(r=>r.id===previous))element.value=previous;
  }
  function validation(){
    const model=data?.models.find(m=>m.id===get('reward-model').value),v=model?.validation;
    set('reward-validation',v?`独立验证 ${v.samples} 帧 · 误报成功 ${v.false_positive} · 漏报成功 ${v.false_negative} · Precision ${(v.precision*100).toFixed(1)}% · Recall ${(v.recall*100).toFixed(1)}% · 阈值 ${model.threshold}。请检查孔口卡住是否被误报为成功。`:'分类器训练后显示独立验证结果。');controls();
  }
  async function frame(){
    const index=Number(get('reward-frame').value),row=data?.frames[index],request=++frameRequest;
    frameReady=false;controls();get('reward-image-1').removeAttribute('src');get('reward-image-2').removeAttribute('src');
    get('reward-review').hidden=!row;if(!row)return;
    set('reward-frame-note',`${index+1}/${data.frames.length} · ${data.groups[row.group]==='train'?'训练':'验证'} · ${row.label===1?'完全到位':row.label===0?'未成功':'未标注'} · ${row.group.slice(0,30)}`);
    try{const images=await PilotAPI.rewardFrame(row.id);if(request!==frameRequest)return;get('reward-image-1').src=images.wrist_1;get('reward-image-2').src=images.wrist_2;frameReady=true;controls();}
    catch(error){set('reward-error',error.message);}
  }
  async function refresh(){
    if(loading || PilotAPI.isDemo)return;loading=true;
    try{
      data=await PilotAPI.reward();loadedAt=Date.now();
      options('reward-source',data.sources);options('reward-model',data.models.filter(m=>m.compatible));
      const c=data.counts;set('reward-counts',`训练：成功 ${c.train['1']} / 未成功 ${c.train['0']}；验证：成功 ${c.validation['1']} / 未成功 ${c.validation['0']}。共 ${data.frames.length} 帧。`);
      set('reward-status',(data.active?`当前分类器：${data.active.name} · 阈值 ${data.active.threshold}`:'尚未选用分类器，HIL-SERL 不会使用人工奖励代替。')+(data.running?' · 正在训练…':''));
      if(data.training?.phase==='error')set('reward-error',data.training.error);
      get('reward-frame').max=Math.max(0,data.frames.length-1);validation();await frame();
    }catch(error){set('reward-error',error.message);}finally{loading=false;controls();}
  }
  async function action(value,advance=false){
    if(busy || PilotAPI.isDemo)return;busy=true;controls();set('reward-error','');
    try{await PilotAPI.rewardAction(value);if(advance)get('reward-frame').value=Math.min(Number(get('reward-frame').value)+1,Math.max(0,data.frames.length-1));await refresh();if(value.action==='activate')get('models-refresh').click();}
    catch(error){set('reward-error',error.message);}finally{busy=false;controls();}
  }
  function render(state){
    active=!!state.experiment?.active;
    const visible=!!state.runtime?.autoserl_demo && get('training-algorithm').value==='hilserl';
    get('reward-workbench').hidden=!visible;
    if(visible && (!data || (data.running && Date.now()-loadedAt>4000)))refresh();controls();
  }
  function init(){
    const newGroup=()=>{get('reward-group').value='round-'+Date.now();};newGroup();
    get('reward-new-group').addEventListener('click',newGroup);
    get('reward-refresh').addEventListener('click',refresh);
    get('reward-frame').addEventListener('input',frame);get('reward-model').addEventListener('change',validation);
    get('reward-import').addEventListener('click',()=>action({action:'import',id:get('reward-source').value,split:get('reward-split').value}));
    for(const [id,label] of [['positive',1],['negative',0],['skip',null]])get('reward-'+id).addEventListener('click',()=>{const row=data?.frames[Number(get('reward-frame').value)];if(row)action({action:'label',id:row.id,label},true);});
    for(const [id,label] of [['positive',1],['negative',0]])get('reward-capture-'+id).addEventListener('click',()=>action({action:'capture',group:get('reward-group').value,split:get('reward-split').value,label}));
    get('reward-train').addEventListener('click',()=>action({action:'train',epochs:Number(get('reward-epochs').value),threshold:Number(get('reward-threshold').value)}));
    get('reward-activate').addEventListener('click',()=>action({action:'activate',id:get('reward-model').value}));
  }
  return {init,render};
})();
