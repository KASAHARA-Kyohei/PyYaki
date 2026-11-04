// Maintainer-only probe. Native time controls transitions even when JS is throttled.
import { pyyaki } from '/_pyyaki/sdk.js';

const report = (phase, data) => window.ipc.postMessage(JSON.stringify({
  v: 1, type: 'pyyaki_diagnostic', phase, data,
}));
const state = () => ({visibility: document.visibilityState, focused: document.hasFocus()});
const segments = [];
const errors = [];
let current;
let heartbeat;
let done = false;
let rpcLoop;
let lastTick;
const status = document.querySelector('#status');

function transition() {
  if (current) current.transitions.push({elapsed_ms: performance.now() - current.started, ...state()});
}
document.addEventListener('visibilitychange', transition);
window.addEventListener('focus', transition);
window.addEventListener('blur', transition);

function closeSegment() {
  clearInterval(heartbeat);
  if (!current) return;
  current.duration_ms = performance.now() - current.started;
  current.final_state = state();
  segments.push(current);
  current = undefined;
}

window.addEventListener('__pyyaki_timer_phase__', async ({detail}) => {
  try {
    if (detail.index !== segments.length + (current ? 1 : 0)) throw Error('Phase order mismatch');
    closeSegment();
    report('timer_phase_seen', {index: detail.index, name: detail.name,
      native_start_ms: detail.native_start_ms, ...state()});
    if (detail.name === 'done') {
      done = true;
      await rpcLoop;
      await pyyaki.invoke('probe.echo', {value: 'after-window-cycle'});
      const info = await pyyaki.invoke('probe.info');
      report('result', {ok: segments.length === 7 && errors.length === 0,
        segments, errors, resources: [info], state: pyyaki.state});
      return;
    }
    current = {index: detail.index, name: detail.name, started: performance.now(),
      native_start_ms: detail.native_start_ms, initial_state: state(), transitions: [],
      timer_samples: [], rpc_ms: [], crossing_rpcs: 0};
    lastTick = performance.now();
    heartbeat = setInterval(() => {
      const now = performance.now();
      current.timer_samples.push({lag_ms: Math.max(0, now - lastTick - 100),
        elapsed_ms: now - current.started, ...state()});
      lastTick = now;
      status.textContent = `${current.name}: ${current.timer_samples.length}`;
    }, 100);
  } catch (error) {
    done = true;
    clearInterval(heartbeat);
    report('result', {ok: false, error: String(error)});
  }
});

try {
  await pyyaki.ready();
  rpcLoop = (async () => {
    let sequence = 0;
    while (!done) {
      const batchStart = performance.now();
      const segment = current;
      if (segment) {
        await Promise.all([0, 1, 2, 3].map(async index => {
          const value = `timer-${sequence}-${index}`;
          const start = performance.now();
          try {
            const echoed = await pyyaki.invoke('probe.echo', {value});
            if (echoed !== value) throw Error('Echo mismatch');
            if (current === segment) segment.rpc_ms.push(performance.now() - start);
            else segment.crossing_rpcs++;
          } catch (error) { errors.push(String(error)); }
        }));
        sequence++;
      }
      await new Promise(resolve => setTimeout(resolve,
        Math.max(0, 1000 - (performance.now() - batchStart))));
    }
  })();
  report('timer_probe_ready', {});
} catch (error) {
  done = true;
  clearInterval(heartbeat);
  report('result', {ok: false, error: String(error)});
}
