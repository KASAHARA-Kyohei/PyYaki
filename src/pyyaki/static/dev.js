// Injected by `pyyaki dev` only; independent of the public RPC SDK.
const revision = new URL(import.meta.url).searchParams.get("revision");

async function poll() {
  try {
    const response = await fetch("/_pyyaki/dev", {
      credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(2000),
    });
    if (response.ok && (await response.json()).revision !== revision) {
      location.reload();
      return;
    }
  } catch {
    // Keep the page during a restart or syntax error; retry after the server recovers.
  }
  setTimeout(poll, 500);
}

setTimeout(poll, 500);
