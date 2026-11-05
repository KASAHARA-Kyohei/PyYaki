import { pyyaki } from '/_pyyaki/sdk.js';
import { scenario } from '/config.js';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = (phase, data) => window.ipc.postMessage(JSON.stringify({
  v: 1, type: 'pyyaki_diagnostic', phase, data,
}));
const require = (value, message) => { if (!value) throw Error(message); };
let done = false;
function finish(ok, details) {
  if (done) return;
  done = true;
  report('result', {ok, scenario, ...details});
}
const expectedFailure = {
  'ui-json': 'EOF while parsing',
  'ui-type': 'Invalid native UI message',
  'worker-crash': '',
  'pipe-close': '',
  'import-timeout': 'timed out',
  'worker-frame-zero': 'invalid frame size',
  'worker-frame-large': 'invalid frame size',
  'worker-frame-json': 'EOF while parsing',
};
window.addEventListener('__pyyaki_backend_failed__', () => {
  setTimeout(() => {
    const text = document.querySelector('[role=alert]')?.textContent ?? '';
    const expected = expectedFailure[scenario];
    finish(expected !== undefined && text.includes(expected) && text.includes('worker.log') &&
      text.includes('host.log'), {failure_text: text});
  }, 100);
}, {once: true});

try {
  await pyyaki.ready();
  require(await pyyaki.invoke('probe.echo', {value: '接続 😀'}) === '接続 😀', 'RPC mismatch');
  report('ready', {scenario});
  if (scenario === 'ui-json') window.ipc.postMessage('{');
  else if (scenario === 'ui-type') window.ipc.postMessage(JSON.stringify({v: 1, type: 'unknown'}));
  else if (scenario === 'worker-crash' || scenario === 'pipe-close') {
    const method = scenario === 'worker-crash' ? 'probe.crash' : 'probe.pipe-close';
    const pending = pyyaki.invoke(method, {}, {timeoutMs: 2000}).catch(error => ({code: error.code}));
    const outcome = await pending;
    report('sample', {pending: outcome});
    if (outcome?.code === 'TIMEOUT') finish(false, {
      limitation: 'Worker write fd closed but EOF not observed before the call deadline',
      pending: outcome,
    });
  } else if (scenario === 'assets') {
    const paths = ['/probe.css', '/missing.css', '/%2e%2e/private.css', '/%252e%252e/private.css',
      '/private%5csecret.css', '/C%3a/secret.css', '/_pyyaki/unknown.css', '/.secret.css'];
    const responses = [];
    // fetch is blocked by connect-src 'none'. Use same-origin stylesheets as asset requests.
    for (const path of paths) {
      const link = document.createElement('link');
      link.rel = 'stylesheet'; link.href = path;
      const result = new Promise(resolve => {
        link.onload = () => resolve('loaded'); link.onerror = () => resolve('rejected');
      });
      document.head.append(link);
      const outcome = await Promise.race([result, sleep(2000).then(() => 'timeout')]);
      const timing = performance.getEntriesByName(link.href).at(-1);
      responses.push({path, result: outcome, status: timing?.responseStatus ?? null});
      link.remove();
    }
    report('sample', {responses});
    require(responses[0].result === 'loaded' && responses[0].status === 200, 'Missing asset control');
    require(responses.slice(1).every(row => row.result === 'rejected'), 'Private CSS loaded');
    require(await pyyaki.invoke('probe.echo', {value: 'after-assets'}) === 'after-assets', 'RPC lost');
    finish(true, {responses, scope: 'Stylesheet loads rejected; error HTTP status is not exposed'});
  } else if (scenario === 'navigation') {
    const initial = location.href;
    const windows = [window.open('/other.html', '_blank'), window.open('https://example.invalid/', '_blank')];
    const frame = document.createElement('iframe'); frame.src = '/other.html';
    document.body.append(frame);
    location.href = '/other.html';
    await sleep(700);
    require(location.href === initial, 'Local navigation escaped');
    location.href = 'https://example.invalid/';
    await sleep(700);
    require(location.href === initial, 'External navigation escaped');
    let frameBlocked = false;
    try { frameBlocked = !frame.contentDocument?.querySelector('#should-not-load'); }
    catch { frameBlocked = true; }
    require(frameBlocked, 'Iframe loaded');
    require(windows.every(value => value === null || value.closed), 'Popup remained open');
    require(await pyyaki.invoke('probe.echo', {value: 'after-navigation'}) === 'after-navigation', 'RPC lost');
    finish(true, {same_location: true, iframe_blocked: frameBlocked, popup_denied: true});
  } else if (scenario === 'oversize') {
    window.ipc.postMessage('x'.repeat(1048577));
    await sleep(200);
    require(await pyyaki.invoke('probe.echo', {value: 'after-oversize'}) === 'after-oversize', 'RPC lost');
    finish(true, {oversize_ignored: true});
  } else if (scenario === 'invalid-proxy') {
    // Allow the external observer to sample owned process/network state.
    await sleep(20000);
    require(await pyyaki.invoke('probe.echo', {value: 'proxy-日本語'}) === 'proxy-日本語', 'RPC lost');
    finish(true, {local_rpc: true});
  } else throw Error('Unknown scenario');
} catch (error) {
  if (!(scenario in expectedFailure)) finish(false, {error: String(error)});
  // Fault scenarios must end through the host-owned failure screen, not a caught promise alone.
}
