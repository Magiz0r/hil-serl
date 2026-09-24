'use strict';
const PilotRecordsUI=(()=>{
  let episode=null,frames=[],timer=null,generation=0,playing=false,videoMode=false,videoSignature='';
  const videos=()=>['external','wrist'].map(name=>byId('video-'+name));
  function stop(){clearTimeout(timer);playing=false;for(const video of videos())video.pause();write('play-recording','播放');}
  function releaseVideos(){videoMode=false;for(const video of videos()){video.removeAttribute('src');video.load();}}
  function renderVideos(){
    const value=episode?.videos || {status:'missing'},signature=JSON.stringify(value);
    if(signature===videoSignature)return;videoSignature=signature;
    const ready=value.status==='ready' && value.urls;
    write('record-video-status',PilotAPI.isDemo?'演示回放 · 不生成真实视频':ready?'MP4 已生成 · 双视角 10 FPS，原始图像与轨迹已保留':value.status==='error'?'MP4 生成失败：'+value.error:['queued','encoding'].includes(value.status)?'MP4 正在后台生成 · 可先加载逐帧回放':'MP4 尚未生成 · 原始图像可回放');
    for(const camera of ['external','wrist']){
      const link=byId('download-'+camera);link.hidden=!ready;
      if(ready){link.href=value.urls[camera];link.download=episode.name+'-'+camera+'.mp4';}else link.removeAttribute('href');
    }
    byId('generate-videos').hidden=PilotAPI.isDemo || ready || ['queued','encoding'].includes(value.status);
    write('generate-videos',value.status==='error'?'重新生成 MP4':'生成 MP4');
    if(value.status==='error')log('视频导出失败：'+value.error,'bad');
  }
  function update(items){
    if(!episode || !byId('episode-dialog').open)return;
    const latest=items?.find(item=>item.id===episode.id && item.name===episode.name);
    if(latest){episode={...episode,videos:latest.videos};renderVideos();}
  }
  function videoTime(){
    if(!videoMode)return;
    const [master,wrist]=videos(),seconds=master.currentTime;
    if(wrist.readyState>=2 && Math.abs(wrist.currentTime-seconds)>.2)wrist.currentTime=seconds;
    byId('playback-seek').value=Math.round(seconds*1000);
    write('playback-time',seconds.toFixed(1)+' / '+Number(episode.videos.duration_seconds).toFixed(1)+' s');
  }
  function show(index){
    const frame=frames[index];if(!frame)return;
    byId('playback-seek').value=index;
    for(const camera of ['external','wrist'])byId('playback-'+camera).src=frame[camera];
    write('playback-time',frame.seconds.toFixed(1)+' / '+frames.at(-1).seconds.toFixed(1)+' s');
    if(playing && index<frames.length-1)timer=setTimeout(()=>show(index+1),Math.max(30,(frames[index+1].seconds-frame.seconds)*1000));
    else if(playing)stop();
  }
  function open(item){
    stop();releaseVideos();generation++;episode=item;frames=[];videoSignature='';renderVideos();
    const available=PilotAPI.isDemo || !!item.id;
    byId('episode-review').hidden=!available;byId('episode-playback').hidden=true;
    byId('record-notes').value=item.notes || '';byId('record-prompt').value=item.prompt || '';
    byId('record-outcome').value=item.outcome;byId('record-outcome').disabled=item.outcome==='incomplete';
    byId('load-recording').disabled=false;byId('save-review').disabled=false;byId('delete-record').disabled=false;
    byId('confirm-record-delete').hidden=true;write('record-error','');
  }
  async function review(action){
    const item=episode,version=generation;
    byId('save-review').disabled=true;byId('delete-record').disabled=true;
    try{
      const episodes=await PilotAPI.review({action,id:item.id || item.name,outcome:byId('record-outcome').value,notes:byId('record-notes').value,prompt:byId('record-prompt').value});
      if(current)current.episodes=episodes;renderEpisodes(true);log(action==='delete'?'记录已移出列表，原始文件保留':'记录判定与备注已保存','good');
      if(version!==generation)return;
      if(action==='delete')byId('episode-dialog').close();else {
        const saved=episodes.find(e=>item.id?e.id===item.id:e.name===item.name);
        if(saved){episode=saved;renderEpisodeDetail(saved);}
        feedback('记录修改已保存','good');
      }
    }catch(error){if(version===generation)write('record-error',error.message);log('记录修改失败：'+error.message,'bad');}
    finally{if(version===generation){byId('save-review').disabled=false;byId('delete-record').disabled=false;}}
  }
  function init(){
    byId('episode-dialog').addEventListener('close',()=>{stop();releaseVideos();generation++;});
    byId('generate-videos').addEventListener('click',async()=>{
      const version=generation;byId('generate-videos').disabled=true;
      try{
        const items=await PilotAPI.review({action:'export_video',id:episode.id});
        if(current)current.episodes=items;
        if(version===generation)update(items);
        log('已请求生成双视角 MP4','info');
      }catch(error){if(version===generation)write('record-error',error.message);}
      finally{byId('generate-videos').disabled=false;}
    });
    byId('load-recording').addEventListener('click',async()=>{
      const version=generation;byId('load-recording').disabled=true;write('record-error','');
      try{
        stop();releaseVideos();
        videoMode=episode.videos?.status==='ready' && !!episode.videos.urls;
        for(const camera of ['external','wrist']){
          byId('playback-'+camera).hidden=videoMode;byId('video-'+camera).hidden=!videoMode;
        }
        if(videoMode){
          for(const camera of ['external','wrist'])byId('video-'+camera).src=episode.videos.urls[camera];
          byId('playback-seek').max=Math.round(episode.videos.duration_seconds*1000);
          byId('playback-seek').value=0;byId('episode-playback').hidden=false;videoTime();return;
        }
        const result=await PilotAPI.frames(episode.id || episode.name);if(version!==generation)return;
        if(!result.length)throw Error('本条记录没有可回放的画面');
        frames=result;byId('episode-playback').hidden=false;byId('playback-seek').max=frames.length-1;show(0);
      }catch(error){if(version===generation)write('record-error',error.message);}
      finally{if(version===generation)byId('load-recording').disabled=false;}
    });
    byId('play-recording').addEventListener('click',async()=>{
      if(playing){stop();return;}playing=true;write('play-recording','暂停');
      if(videoMode){
        if(videos()[0].ended)for(const video of videos())video.currentTime=0;
        try{await Promise.all(videos().map(video=>video.play()));}catch(error){stop();write('record-error','视频播放失败：'+error.message);}return;
      }
      const index=Number(byId('playback-seek').value);show(index>=frames.length-1?0:index);
    });
    byId('playback-seek').addEventListener('input',()=>{
      stop();const value=Number(byId('playback-seek').value);
      if(videoMode){for(const video of videos())video.currentTime=value/1000;videoTime();}else show(value);
    });
    byId('video-external').addEventListener('timeupdate',videoTime);
    byId('video-external').addEventListener('ended',stop);
    for(const video of videos())video.addEventListener('error',()=>{if(videoMode){stop();write('record-error','MP4 无法播放，请检查视频文件或连接。');}});
    for(const camera of ['external','wrist'])byId('playback-'+camera).addEventListener('error',()=>{stop();write('record-error','回放图像不可用，请检查记录文件或连接。');});
    byId('save-review').addEventListener('click',()=>review('update'));
    byId('delete-record').addEventListener('click',()=>{byId('confirm-record-delete').hidden=false;});
    byId('cancel-record-delete').addEventListener('click',()=>{byId('confirm-record-delete').hidden=true;});
    byId('accept-record-delete').addEventListener('click',()=>review('delete'));
  }
  return {init,open,update};
})();
