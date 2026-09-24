import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
// Requires the isolated preview server; never point this at a live recorder.
const base='http://127.0.0.1:'+(process.env.PILOT_PREVIEW_PORT || '8766');
const dir=await mkdtemp(join(tmpdir(),'tasl-ui-cdp-'));
const output=process.env.PILOT_SCREENSHOT_DIR || '/tmp/tasl-capture-ui-check';await mkdir(output,{recursive:true});
const chrome=spawn('/usr/bin/google-chrome',['--headless','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-port=0','--user-data-dir='+dir,'about:blank'],{stdio:['ignore','ignore','pipe']});
let errors='';chrome.stderr.on('data',data=>{errors+=data;});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let ws;
try {
 let port;
 for(let i=0;i<100;i++) {try{port=(await readFile(join(dir,'DevToolsActivePort'),'utf8')).split('\n')[0];break;}catch(_){await delay(50);}}
 if(!port) throw Error(errors);
 const targets=await (await fetch('http://127.0.0.1:'+port+'/json')).json();
 ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
 let sequence=0;const pending=new Map();const consoleErrors=[];const requests=[];
 ws.onmessage=event=>{const packet=JSON.parse(event.data);if(packet.id){const p=pending.get(packet.id);pending.delete(packet.id);packet.error?p.reject(Error(JSON.stringify(packet.error))):p.resolve(packet.result);}else if(packet.method==='Runtime.exceptionThrown') consoleErrors.push(packet.params.exceptionDetails);else if(packet.method==='Network.requestWillBeSent') requests.push(packet.params.request);};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
 const reports=[];
 for(const [width,height] of [[1920,1080],[1440,1000],[1024,900],[390,844]]){
  await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<600});
  await call('Page.navigate',{url:base+'/?demo=1'});await delay(850);
  const result=await call('Runtime.evaluate',{expression:`JSON.stringify({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,state:document.body.dataset.state,startTop:document.getElementById('start').getBoundingClientRect().top,recordsTop:document.querySelector('.records-panel').getBoundingClientRect().top,assets:[...document.scripts].map(s=>s.src),bodyHeight:document.documentElement.scrollHeight})`,returnByValue:true});
  const report=JSON.parse(result.result.value);reports.push(report);
  if(report.width!==width || report.scrollWidth>width || report.state!=='idle') throw Error('layout or script loading failed: '+JSON.stringify(report));
  if(width<600){const dock=await call('Runtime.evaluate',{expression:"document.getElementById('mobile-start').getBoundingClientRect().bottom<=innerHeight",returnByValue:true});if(!dock.result.value)throw Error('mobile controls outside viewport');}
  const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width,height:report.bodyHeight,scale:1}});
  await writeFile(join(output,'served-'+width+'.png'),Buffer.from(shot.data,'base64'));
 }
 for(const scene of ['running','saving','homing','error','offline']){
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:base+'/?demo=1&scene='+scene});await delay(700);
  const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,'state-'+scene+'.png'),Buffer.from(shot.data,'base64'));
 }
 if(consoleErrors.length)throw Error(JSON.stringify(consoleErrors));
 if(requests.some(r=>r.method==='POST'||/\/command|\/status|\/camera\//.test(r.url))) throw Error('demo contacted live API');
 await writeFile(join(output,'layout-report.json'),JSON.stringify({reports,consoleErrors,deviceRequests:0},null,2));
 console.log(JSON.stringify({reports,consoleErrors,deviceRequests:0},null,2));
} finally {if(ws)ws.close();chrome.kill('SIGTERM');}
