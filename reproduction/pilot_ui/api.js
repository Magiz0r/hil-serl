'use strict';
// Device commands, service connection, and metadata edits use distinct APIs.
const PilotAPI = (() => {
  const isDemo = new URLSearchParams(location.search).get('demo') === '1';
  async function request(path, options = {}) {
    const response = await fetch(path, {cache: 'no-store', ...options, signal: AbortSignal.timeout(2000)});
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try { message = (await response.json()).error || message; } catch (_) { /* non-JSON error */ }
      throw Error(message);
    }
    return response.json();
  }
  return {
    isDemo,
    status: () => isDemo ? PilotDemo.status() : request('/status'),
    catalog: data => isDemo ? PilotDemo.catalog(data) : request('/catalog', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
    }),
    runtime: action => request('/runtime',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})}),
    frames: id => isDemo ? Promise.resolve([{seconds:0,external:PilotDemo.image('external'),wrist:PilotDemo.image('wrist')}]) : request('/episodes/'+id+'/frames'),
    review: data => isDemo ? PilotDemo.review(data) : request('/episodes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),
    command: command => isDemo ? PilotDemo.command(command) : request('/command', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({command})
    })
  };
})();
