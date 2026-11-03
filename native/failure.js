function renderFailure(details) {
  // Host-owned UI: all exception messages and paths remain text, never markup.
  window.dispatchEvent(new Event('__pyyaki_backend_failed__'));
  const phases = {
    ui_initialization: '画面の準備',
    python_spawn: 'Pythonの起動',
    worker_entered: 'アプリ設定の読み込み',
    config_loaded: 'アプリの読み込み準備',
    app_import_started: 'アプリの読み込み',
    app_imported: '起動処理の準備',
    startup_hooks_started: 'アプリの起動処理',
    startup_hooks_completed: '接続の準備',
    connection: 'Pythonとの接続',
  };
  const element = (tag, text) => {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  };
  document.head.replaceChildren();
  document.documentElement.lang = 'ja';
  const title = element('title', 'PyYaki — エラー');
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = '/_pyyaki/failure.css';
  document.head.append(title, stylesheet);
  const panel = document.createElement('main');
  panel.setAttribute('role', 'alert');
  panel.append(
    element('h1', 'Pythonとの接続を続けられませんでした'),
    element('p', `停止した段階：${phases[details.phase] ?? details.phase}`),
    element('pre', details.message),
    element('h2', '詳細ログ'),
    element('p', '原因の詳細と起動時間は、次のファイルで確認できます。'),
    element('h3', 'Python'), element('pre', details.worker_log),
    element('h3', 'ホスト・起動時間'), element('pre', details.host_log),
    element('p', 'このウィンドウを閉じて終了してください。'),
  );
  document.body.replaceChildren(panel);
}
