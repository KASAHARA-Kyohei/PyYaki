import { pyyaki } from '/_pyyaki/sdk.js';
import config from '/config.js';

const status = document.querySelector('#status');
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
// Enabled explicitly by the maintainer's --diagnostics flag on the Rust host.
const report = (phase, data) => window.ipc.postMessage(JSON.stringify({
  v: 1, type: 'pyyaki_diagnostic', phase, data,
}));
let ticks = 0;
let previous = performance.now();
const timerLag = [];
const heartbeat = setInterval(() => {
  const now = performance.now();
  timerLag.push(Math.max(0, now - previous - 100));
  previous = now;
  status.textContent = `UI heartbeat: ${++ticks}`;
}, 100);
const rpc = [];
const resources = [];
let events = 0;
let cancelled = 0;
let timeouts = 0;
const unsubscribe = pyyaki.on('tick', () => { events++; });

async function echo(value, emit = false) {
  const start = performance.now();
  const result = await pyyaki.invoke('probe.echo', {value, emit});
  if (result !== value) throw Error('Echo mismatch');
  return performance.now() - start;
}

async function cancellation() {
  const controller = new AbortController();
  const promise = pyyaki.invoke('probe.delay', {}, {signal: controller.signal});
  setTimeout(() => controller.abort(), 50);
  try { await promise; throw Error('Missing cancellation'); }
  catch (error) { if (error.code !== 'CANCELLED') throw error; cancelled++; }
  try {
    await pyyaki.invoke('probe.delay', {}, {timeoutMs: 30});
    throw Error('Missing timeout');
  } catch (error) { if (error.code !== 'TIMEOUT') throw error; timeouts++; }
  await echo('after-cancel');
  const info = await pyyaki.invoke('probe.info');
  if (info.active !== 0) throw Error('Cancelled work is still active');
  resources.push(info);
}

try {
  const info = await pyyaki.ready();
  report('ready', {mode: info.mode});
  const firstRpcMs = await echo('first');
  report('interactive', {first_rpc_ms: firstRpcMs});
  const backend = await pyyaki.invoke('probe.info');
  resources.push(backend);

  if (config.scenario === 'freeze') {
    report('fault_ready', backend);
    await sleep(350);
    const before = ticks;
    let code;
    try { await pyyaki.invoke('probe.echo', {value: 'paused'}, {timeoutMs: 250}); }
    catch (error) { code = error.code; }
    const liveTicks = ticks - before;
    report('paused_timeout', {code, live_ticks: liveTicks});
    await echo('after-resume');
    if (code !== 'TIMEOUT' || liveTicks < 1) throw Error('UI stalled with stopped Python');
    report('result', {ok: true, timeout_code: code, live_ticks: liveTicks});
  } else if (config.scenario === 'crash') {
    const pending = pyyaki.invoke('probe.delay', {milliseconds: 10000});
    // Attach the rejection handler before the harness kills the worker.
    const rejected = pending.then(() => 'UNEXPECTED_RESULT', error => error.code);
    await sleep(50);
    report('fault_ready', backend);
    const failureAt = performance.now();
    const code = await rejected;
    const rejectedMs = performance.now() - failureAt;
    const before = ticks;
    await sleep(350);
    report('result', {ok: code === 'BACKEND_FAILED' && ticks > before,
      code, rejection_ms: rejectedMs, live_ticks: ticks - before, state: pyyaki.state});
  } else if (config.scenario === 'shutdown') {
    pyyaki.invoke('probe.delay', {milliseconds: 10000}).catch(() => {});
    await sleep(100);
    report('fault_ready', backend);
  } else {
    for (let index = 0; index < 20; index++) await echo('warmup');
    for (let index = 0; index < config.rpc_samples; index++) rpc.push(await echo('x'.repeat(256)));
    const soak = [];
    const started = performance.now();
    const interval = 1000;
    let batches = 0;
    while (performance.now() - started < config.duration_seconds * 1000) {
      const batchAt = performance.now();
      const samples = await Promise.all([0, 1, 2, 3].map(index => echo(`soak-${batches}-${index}`, index === 0)));
      soak.push(...samples);
      if (++batches % 20 === 0) {
        await cancellation();
        report('sample', {elapsed_ms: performance.now() - started, batches,
          ...resources.at(-1)});
      }
      await sleep(Math.max(0, interval - (performance.now() - batchAt)));
    }
    const deadline = performance.now() + 2000;
    while (events < batches && performance.now() < deadline) await sleep(10);
    resources.push(await pyyaki.invoke('probe.info'));
    if (events !== batches) throw Error('Event count mismatch');
    report('result', {ok: true, first_rpc_ms: firstRpcMs, rpc_ms: rpc, soak_rpc_ms: soak,
      duration_ms: performance.now() - started, events, cancelled, timeouts,
      heartbeat_ticks: ticks, timer_lag_ms: timerLag, resources});
  }
} catch (error) {
  report('result', {ok: false, error: String(error), code: error.code});
} finally {
  if (config.scenario !== 'shutdown') { clearInterval(heartbeat); unsubscribe(); }
}
