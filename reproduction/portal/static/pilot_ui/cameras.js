'use strict';
const PilotCameras = (() => {
  const views={};
  let liveStatus=null, connected=false;
  function decorate(name) {
    const view=views[name];
    if (!view) return;
    const age=liveStatus?.camera_ages?.[name];
    const error=liveStatus?.camera_errors?.[name];
    const fresh=typeof age==='number' && age>=0 && age<=.35;
    const recent=performance.now()-view.lastFrame<2200;
    const healthy=connected && fresh && recent && !error && !liveStatus?.fatal;
    const loading=!view.lastFrame && connected && age == null && !error && !liveStatus?.fatal;
    view.figure.classList.toggle('has-frame',!!view.lastFrame);
    view.figure.classList.toggle('stale',!healthy);
    const badge=document.getElementById(name+'-badge');
    badge.className='badge '+(healthy?'good':connected && error?'bad':loading?'muted':'warn');
    badge.replaceChildren(Object.assign(document.createElement('i'),{}),document.createTextNode(healthy?'LIVE':!connected?'离线':error?'异常':loading?'连接中':'离线'));
    view.placeholder.hidden=healthy;
    view.placeholder.querySelector('strong').textContent=!connected?'连接中断 · 画面已过期':error?'相机不可用':loading?'正在连接相机':'相机断流 · 等待恢复';
    view.placeholder.querySelector('span').textContent=connected && error?error+'；处理后点击「重连相机」':loading?'等待第一帧画面':view.lastFrame?'保留的最后一帧不是实时画面':'尚未收到可用画面';
    document.getElementById(name+'-age').textContent=connected && typeof age==='number' ? Math.round(age*1000)+' ms' : '— ms';
    view.figure.querySelector('.record-indicator').hidden=!(healthy && liveStatus?.recording);
  }
  function start(name) {
    const img=document.getElementById(name);
    const view=views[name]={img,figure:document.getElementById('camera-'+name),placeholder:document.getElementById(name+'-placeholder'),lastFrame:0};
    view.figure.querySelector('.camera-watermark').hidden=!PilotAPI.isDemo;
    if (PilotAPI.isDemo) {
      img.onload=()=>{view.lastFrame=performance.now();decorate(name);};
      img.src=PilotDemo.image(name);
      return;
    }
    const framePeriod=1000/15;
    let nextTimer, deadline, requestedAt=0;
    const next=delay=>{clearTimeout(nextTimer);clearTimeout(deadline);nextTimer=setTimeout(update,delay);};
    const update=()=>{
      if(document.hidden){next(1000);return;}
      requestedAt=performance.now();
      img.src='/camera/'+name+'.jpg?t='+Date.now();
      deadline=setTimeout(()=>{view.lastFrame=0;decorate(name);next(1000);},2000);
    };
    // Include transfer/decode time in the frame period; keep only one request
    // in flight, without accumulating an extra delay after every image.
    img.onload=()=>{view.lastFrame=performance.now();decorate(name);next(Math.max(0,framePeriod-(view.lastFrame-requestedAt)));};
    img.onerror=()=>{view.lastFrame=0;decorate(name);next(1000);};
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden){clearTimeout(nextTimer);}
      else next(0);
    });
    update();
  }
  function update(status,online) {
    liveStatus=status;connected=online;
    for(const name of ['external','wrist']) {
      if(PilotAPI.isDemo && views[name]?.img.complete) views[name].lastFrame=performance.now();
      decorate(name);
    }
  }
  return {start(){start('external');start('wrist');},update};
})();
