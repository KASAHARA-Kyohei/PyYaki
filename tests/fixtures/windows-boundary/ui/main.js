import { pyyaki } from '/_pyyaki/sdk.js';
import { scenario } from '/config.js';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = (phase, data) => window.ipc.postMessage(JSON.stringify({
  v: 1, type: 'pyyaki_diagnostic', phase, data,
}));
const require = (value, message) => { if (!value) throw Error(message); };
let done = false;
let reachedReady = false;
let injected = false;
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
    const afterReady = ['ui-json', 'ui-type', 'worker-crash', 'pipe-close'].includes(scenario);
    const phase = afterReady ? 'Pythonとの接続' :
      scenario === 'import-timeout' || scenario.startsWith('worker-frame-') ? 'アプリの読み込み' : '';
    finish(expected !== undefined && text.includes(expected) && text.includes('worker.log') &&
      text.includes('host.log') && text.includes(`停止した段階：${phase}`) &&
      (!afterReady || (reachedReady && injected)), {failure_text: text, reachedReady, injected});
  }, 100);
}, {once: true});

try {
  await pyyaki.ready();
  reachedReady = true;
  require(await pyyaki.invoke('probe.echo', {value: '接続 😀'}) === '接続 😀', 'RPC mismatch');
  report('ready', {scenario});
  if (scenario === 'ui-json') { injected = true; window.ipc.postMessage('{'); }
  else if (scenario === 'ui-type') {
    injected = true; window.ipc.postMessage(JSON.stringify({v: 1, type: 'unknown'}));
  }
  else if (scenario === 'worker-crash' || scenario === 'pipe-close') {
    const method = scenario === 'worker-crash' ? 'probe.crash' : 'probe.pipe-close';
    injected = true;
    const pending = pyyaki.invoke(method, {}, {timeoutMs: 2000}).catch(error => ({code: error.code}));
    const outcome = await pending;
    report('sample', {pending: outcome});
    if (outcome?.code === 'TIMEOUT') finish(false, {
      limitation: 'Worker write fd closed but EOF not observed before the call deadline',
      pending: outcome,
    });
  } else if (scenario === 'worker-grandchild' || scenario === 'host-kill-tree') {
    const child = await pyyaki.invoke('probe.spawn-child');
    report('fault_ready', child);
    if (scenario === 'worker-grandchild') {
      await sleep(1200);
      finish(true, {child});
    }
  } else if (scenario === 'handle-inheritance') {
    const proof = JSON.parse(await pyyaki.invoke('probe.sentinel'));
    require(proof.control_matches, 'Missing positive identity control');
    require(!proof.inherited, 'Unlisted host handle reached worker');
    finish(true, {proof});
  } else if (scenario === 'user-actions') {
    const clicks = [];
    document.querySelector('#status').textContent = '接続済み：検証ボタンを順にクリック';
    const button = (text, action) => {
      const node = document.createElement('button'); node.textContent = text;
      node.addEventListener('click', async event => {
        const click = {text, trusted: event.isTrusted, active: navigator.userActivation.isActive};
        clicks.push(click);
        action();
        report('sample', {click});
        node.disabled = true;
        if (clicks.length === 3) {
          await sleep(1000);
          require(await pyyaki.invoke('probe.echo', {value: 'after-user-actions'}) ===
            'after-user-actions', 'RPC lost');
          finish(clicks.every(value => value.trusted && value.active), {clicks});
        }
      }, {once: true});
      document.body.append(node);
    };
    button('新規ウィンドウの拒否を確認', () => window.open('/other.html', '_blank'));
    button('ローカル資産のダウンロード拒否を確認', () => {
      const link = document.createElement('a'); link.href = '/probe.css';
      link.download = 'pyyaki-boundary-local.css'; link.click();
    });
    button('data URLのダウンロード拒否を確認', () => {
      const link = document.createElement('a'); link.href = 'data:text/plain;base64,UHlZYWtp';
      link.download = 'pyyaki-boundary-data.txt'; link.click();
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
