'use strict';
const PilotCatalogUI = (() => {
  let busy=false,dirty=false,selection='',dialogAction='',deleteAction=null,optionSignature='';
  const catalog=()=>current?.catalog;
  function options(element,items,selected,emptyLabel) {
    element.replaceChildren(...(emptyLabel?[new Option(emptyLabel,'')]:[]),...items.map(item=>new Option(item.label,item.id)));
    element.value=selected || '';
  }
  async function apply(data) {
    if(busy) return false;
    busy=true;localError='';renderControls();
    try {
      const result=await PilotAPI.catalog(data);
      if(current){current.catalog=result;current.task=result.tasks.find(t=>t.id===result.task_id) || null;current.prompt=result.prompt;current.layout=result.layouts.find(l=>l.id===result.layout_id) || null;}
      dirty=false;selection='';log('任务 / Layout 已更新','good');feedback('采集配置已保存','good');render();return true;
    } catch(error) {localError=error.message;log('配置更新失败：'+error.message,'bad');write('catalog-form-error',error.message);byId('task-select').value=catalog()?.task_id || '';byId('layout-select').value=catalog()?.layout_id || '';render();return false;}
    finally {busy=false;renderControls();render(current);}
  }
  function choose(taskId,layoutId,prompt) {return apply({action:'select',task_id:taskId,layout_id:layoutId,prompt});}
  function open(action) {
    dialogAction=action;const c=catalog();
    byId('catalog-task-fields').hidden=action!=='task_create';byId('catalog-ood-field').hidden=action!=='layout_capture';
    byId('catalog-prompt').required=action==='task_create';
    write('catalog-dialog-title',{task_create:'新建采集任务',layout_capture:'拍摄当前场景',layout_rename:'重命名 Layout'}[action]);
    byId('catalog-name').value=action==='layout_rename'?c.layouts.find(l=>l.id===c.layout_id)?.name || '':'';
    byId('catalog-prompt').value='';byId('catalog-success').value='';byId('catalog-ood').checked=false;write('catalog-form-error','');
    byId('catalog-dialog').showModal();byId('catalog-name').focus();
  }
  function confirm(action) {
    deleteAction=action;
    write('catalog-confirm-message',action==='task_delete'?'删除此任务后，它将从可选列表移除，已保存的采集记录及任务快照会保留。':'删除此 Layout 后将停止叠图并从列表移除，已采集记录中的场景快照会保留。');
    byId('catalog-confirm').showModal();
  }
  function ghost() {
    const c=catalog(),id=c?.layout_id,image=byId('layout-ghost');
    const source=id?(PilotAPI.isDemo?PilotDemo.image('external'):'/layouts/'+id+'/external.jpg'):'';
    const visible=!!source && byId('ghost-enabled').checked;
    image.hidden=!visible;image.style.opacity=String(Number(byId('ghost-opacity').value)/100);
    if(source && image.dataset.source!==source){image.dataset.source=source;image.src=source;}
    byId('export-layout').hidden=!source;byId('export-layout').href=source || '#';
    byId('export-layout').download=(id || 'layout')+'-external.jpg';
    try{localStorage.setItem('capture-ghost',JSON.stringify({enabled:byId('ghost-enabled').checked,opacity:byId('ghost-opacity').value}));}catch(_){}
  }
  function renderCatalog(s=current) {
    const c=s?.catalog;if(!c){for(const id of ['task-select','new-task','delete-task','layout-select','capture-layout','rename-layout','delete-layout','prompt-input'])byId(id).disabled=true;return;}
    const signature=JSON.stringify([c.tasks,c.layouts,c.task_id]);
    if(signature!==optionSignature){
      options(byId('task-select'),c.tasks.map(t=>({id:t.id,label:t.display_name || t.name})),c.task_id,'请选择任务');
      options(byId('layout-select'),c.layouts.filter(l=>l.task_id===c.task_id).map(l=>({id:l.id,label:l.name+(l.ood?' · OOD':'')})),c.layout_id,'不关联 Layout');
      optionSignature=signature;
    }
    if(document.activeElement!==byId('task-select'))byId('task-select').value=c.task_id || '';
    if(document.activeElement!==byId('layout-select'))byId('layout-select').value=c.layout_id || '';
    const key=c.task_id+'|'+c.layout_id+'|'+c.prompt;
    if(key!==selection && !dirty){byId('prompt-input').value=c.prompt || '';selection=key;}
    const locked=busy || !!s.recording || !!s.pending || !!awaiting || !!inflight || s.pilot?.mode==='homing';
    for(const id of ['task-select','new-task','delete-task','layout-select','prompt-input'])byId(id).disabled=locked || !connected;
    byId('delete-task').disabled=locked || !c.task_id || !connected;
    for(const id of ['rename-layout','delete-layout'])byId(id).disabled=locked || !c.layout_id || !connected;
    const camerasReady=['external','wrist'].every(name=>typeof s.camera_ages?.[name]==='number' && s.camera_ages[name]>=0 && s.camera_ages[name]<=.35);
    byId('capture-layout').disabled=locked || !c.task_id || !camerasReady || dirty || !connected;
    byId('apply-prompt').disabled=locked || !dirty || !c.task_id || !connected;
    write('prompt-state',dirty?'未应用':'');ghost();
  }
  function init() {
    try{const settings=JSON.parse(localStorage.getItem('capture-ghost') || 'null');if(settings){byId('ghost-enabled').checked=!!settings.enabled;byId('ghost-opacity').value=settings.opacity;}}catch(_){}
    byId('new-task').addEventListener('click',()=>open('task_create'));
    byId('delete-task').addEventListener('click',()=>confirm('task_delete'));
    byId('capture-layout').addEventListener('click',()=>open('layout_capture'));
    byId('rename-layout').addEventListener('click',()=>open('layout_rename'));
    byId('delete-layout').addEventListener('click',()=>confirm('layout_delete'));
    byId('catalog-cancel').addEventListener('click',()=>byId('catalog-dialog').close());
    byId('catalog-confirm-cancel').addEventListener('click',()=>byId('catalog-confirm').close());
    byId('catalog-confirm-delete').addEventListener('click',async()=>{
      byId('catalog-confirm-delete').disabled=true;
      if(await apply({action:deleteAction,task_id:catalog().task_id,layout_id:catalog().layout_id}))byId('catalog-confirm').close();
      byId('catalog-confirm-delete').disabled=false;
    });
    byId('catalog-form').addEventListener('submit',async event=>{
      event.preventDefault();byId('catalog-submit').disabled=true;
      const data={action:dialogAction,name:byId('catalog-name').value,prompt:byId('catalog-prompt').value,success_definition:byId('catalog-success').value,ood:byId('catalog-ood').checked,layout_id:catalog()?.layout_id};
      if(await apply(data))byId('catalog-dialog').close();byId('catalog-submit').disabled=false;
    });
    byId('task-select').addEventListener('change',()=>{const task=catalog().tasks.find(t=>t.id===byId('task-select').value);if(task){dirty=false;choose(task.id,null,task.prompt);}else render();});
    byId('layout-select').addEventListener('change',()=>choose(catalog().task_id,byId('layout-select').value,byId('prompt-input').value));
    byId('prompt-input').addEventListener('input',()=>{dirty=byId('prompt-input').value!==catalog()?.prompt;renderControls();render();});
    byId('apply-prompt').addEventListener('click',()=>apply({action:'task_update',task_id:catalog().task_id,prompt:byId('prompt-input').value,success_definition:current?.task?.success_definition || ''}));
    for(const id of ['ghost-enabled','ghost-opacity'])byId(id).addEventListener('input',ghost);
    byId('layout-ghost').addEventListener('error',()=>{byId('layout-ghost').hidden=true;feedback('Layout 参考图加载失败','warn');});
  }
  return {init,render:renderCatalog,blocksStart:()=>busy || dirty || (catalog() && !catalog().task_id)};
})();
