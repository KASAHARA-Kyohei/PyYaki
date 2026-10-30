import { pyyaki } from "/_pyyaki/sdk.js";

const connection = document.querySelector("#connection");
const result = document.querySelector("#result");
const error = document.querySelector("#error");
const timing = document.querySelector("#timing");
const activity = document.querySelector("#activity");
const commands = [...document.querySelectorAll("#greet, #wait, #fail")];
let active = 0;
let count = 0;

document.querySelector("#counter").addEventListener("click", () => {
  document.querySelector("#count").textContent = String(++count);
});

async function call(name, params, button) {
  const start = performance.now();
  active++;
  button.disabled = true;
  activity.textContent = "WORKING";
  result.textContent = "Pythonで処理しています…";
  result.hidden = false;
  error.hidden = true;
  try {
    const response = await pyyaki.invoke(name, params);
    result.textContent = response.message;
    result.hidden = false;
    error.hidden = true;
  } catch (failure) {
    result.hidden = true;
    error.textContent = `${failure.code}: ${failure.message}`;
    error.hidden = false;
  } finally {
    timing.textContent = `呼び出しの所要時間 ${Math.round(performance.now() - start)} ms`;
    active--;
    activity.textContent = active ? "WORKING" : "READY";
    button.disabled = pyyaki.state !== "ready";
  }
}

document.querySelector("#greet-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (document.querySelector("#greet").disabled) return;
  call("greet", { name: document.querySelector("#name").value }, document.querySelector("#greet"));
});
document.querySelector("#wait").addEventListener("click", (event) => {
  call("wait", { seconds: 3 }, event.currentTarget);
});
document.querySelector("#fail").addEventListener("click", (event) => {
  call("fail", {}, event.currentTarget);
});

try {
  await pyyaki.ready();
  connection.textContent = "Python 接続済み";
  connection.dataset.state = "ready";
  commands.forEach((button) => { button.disabled = false; });
} catch (failure) {
  connection.textContent = "接続できませんでした";
  connection.dataset.state = "failed";
  error.textContent = `${failure.code}: ${failure.message}`;
  error.hidden = false;
}

// Reflect a lost connection even when no command is in progress.
setInterval(() => {
  if (["failed", "disconnected"].includes(pyyaki.state)) {
    connection.textContent = "接続が切れました。再読み込みしてください";
    connection.dataset.state = "failed";
    commands.forEach((button) => { button.disabled = true; });
  }
}, 1000);
