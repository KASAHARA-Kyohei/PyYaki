window.addEventListener('DOMContentLoaded', async () => {
  try {
    const { pyyaki } = await import(new URL('/_pyyaki/sdk.js', location.href).href);
    const info = await pyyaki.ready();
    if (info.mode !== window.__PYYAKI_NATIVE__.mode) throw Error('Wrong native mode');
    const greeting = await pyyaki.invoke('greet', {name: 'Native smoke'});
    if (!greeting.message.includes('Native smoke')) throw Error('Invalid greeting');
    try { await pyyaki.invoke('fail'); throw Error('Missing application error'); }
    catch (error) { if (error.code !== 'APP_DEMO') throw error; }
    const slow = pyyaki.invoke('wait', {seconds: 1});
    await pyyaki.invoke('greet', {name: 'concurrent'});
    await slow;
    const controller = new AbortController();
    const cancelled = pyyaki.invoke('wait', {seconds: 3}, {signal: controller.signal});
    controller.abort();
    try { await cancelled; throw Error('Missing cancellation'); }
    catch (error) { if (error.code !== 'CANCELLED') throw error; }
    window.ipc.postMessage(JSON.stringify({v:1, type:'pyyaki_smoke_done', ok:true}));
  } catch (error) {
    window.ipc.postMessage(JSON.stringify({v:1, type:'pyyaki_smoke_done', ok:false, message:String(error)}));
  }
}, {once:true});
