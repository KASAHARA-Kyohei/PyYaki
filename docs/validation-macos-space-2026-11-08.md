# Mac初期Space配置の対策と比較（2026-11-08）

実施日: 2026-11-08（日本時間）。[直前の回帰確認](validation-macos-2026-11-08.md)後、利用者の依頼でADR-017の再現条件を揃え、対策を比較・実装した。**開発ホストの再現した初回失敗に対するローカル対策であり、全macOS/Spaces条件の解消、物理的な画面提示、Windowsの受入を証明するものではない。**

## 対象・環境と比較方法

着手時は`cf18177`、作業ツリーはクリーン。変更前ホストは前回の`008ec68`由来のrelease（製品コードはcf18177まで同じ）を別名で保存した。検証用ソース・アプリ・故障注入はすべて`test-results/adr017-20261108/`のコピーで行った（以下「保存先」）。比較後に採用した製品修正は`9e9302a1cccf60270ddeeef7dd8814f704bc4f0f`。最終検証はcf18177に製品差分を加えた状態で実行し、3製品ファイルのSHA-256が9e9302aの内容と一致することを照合した。

| 項目 | 今回の値 |
| --- | --- |
| Mac | macOS 27.0.1、26A434 / Apple M2、arm64 |
| Python | 既存venv、CPython 3.14.3、通常GIL、Py_GIL_DISABLED=0 |
| Rust / SDK | 既存Rust/Cargo 1.89.0、aarch64-apple-darwin / コマンド単位のSDKROOTでMacOSX26.5.sdkを指定 |
| GUI依存 | Cargo.lockのtao 0.34.8 / wry 0.53.5、OSのWKWebView |
| 変更前ホスト SHA-256 | `d919d1f3e6a5d046b927ecbc7293eedc23bec59d138dabd120e3f68eafafa406` |
| 最終ホスト SHA-256 | `c1faac88f012ca3cb184581bc41224ec6ec64608511edabd9704e391addaf4be` |
| 今回生成wheel SHA-256 | `562365dd4f732fc4fcfedd49753fd86bd03f28b2e05e632a970b31735025eda7` |

利用者のCodex表示モードの申告は今回も未取得。Codex自身や他アプリを操作する代わりに、保存先のRust fixture `trial-deferred-show/native/examples/space_control.rs`で**自分の検証窓だけ**をネイティブフルスクリーンにした。NSWindowのFullScreen、isOnActiveSpace、Visible遮蔽ビット、NSApplication.isActiveがすべてtrueで2秒安定してから、既存startup_probeを`--runs 1 --delay 0 --watchdog 20`で子起動した。制御窓は子終了後に自分のfullscreenを解除して終了する。OS・保護・プロキシの設定は変更していない。

各組は最初の1件だけがフルスクリーンからの新規起動で、後続のimport/hooksは待機0秒の連続対照。**3件成功を独立したfullscreen開始3回とは数えない。** 各開始直前の全状態はbefore-child.json、制御プロセスの子の成否はcontrol-result.jsonで照合する。制御窓自体の終了コード0だけで成功判定しない。

## 公式資料・既存OSSとの比較

調査日: 2026-11-08。新しいGUIライブラリは追加せず、固定したtao/wryとAppKitの公開APIを比較した。

| 選択肢 | 根拠と今回の判断 |
| --- | --- |
| taoの起動完了後に窓を表示 | 固定版のAppState::launchedは、表示済み窓の再前面化→アプリactivate→Init通知という順序。[公式ソース](https://github.com/tauri-apps/tao/blob/10b26c8712033af65c0721461590a57ec9549bbc/src/platform_impl/macos/app_state.rs)のコメントにも、Initで表示する代替案がある。コピーで試したが初回失敗が残ったため不採用 |
| アプリの自動activateを抑止 | [Tauri runtimeのAPI契約](https://docs.rs/tauri-runtime/latest/src/tauri_runtime/lib.rs.html)と[公式リポジトリの提案経緯](https://github.com/tauri-apps/tauri/issues/15017)を参照。他アプリがactiveなら起動時に自分をactivateしない選択であり、表示可能性とフォーカス方針が変わる。今回は実験・採用していない |
| 起動中だけMoveToActiveSpace | AppKitの[collectionBehavior](https://developer.apple.com/documentation/appkit/nswindow/collectionbehavior-swift.struct)を使用。SDK 26.5のNSWindow.hでもCanJoinAllSpacesとの排他を確認した。最小版で再現した失敗がなくなったため、初期描画までのローカル対策として実装 |
| 全Space・fullscreen補助窓として常時表示 | [Appleのfullscreen説明](https://developer.apple.com/library/archive/documentation/General/Conceptual/MOSXAppProgrammingGuide/FullScreenApp/FullScreenApp.html)にある補助窓等の方針。通常の単一アプリ窓の継続的な振る舞いを変えるため、今回は採らない |
| 非表示待ちと接続期限を分離 | [WebKit公式説明](https://webkit.org/blog/8970/how-web-content-can-affect-power-usage/)は別Space等の非active時にrAF停止・タイマー抑制があると説明する。長い非表示からの復帰には引き続き検討余地があるが、既存SDK/公開ready/UI期限の契約を変える案なので今回は未実装 |

tao起動時の再前面化とSpace配置は調査候補であり、macOS内部の割当変更の原因を特定した、またはtao全体の不具合を証明したとはしない。過去の一度だけのset_focus試作が失敗した[11-04の記録](startup-diagnostics.md#初期描画待ちとspaceの比較2026-11-04)も維持し、繰返し前面化の回避策は追加していない。

## 比較結果

| 対象・条件 | 今回の結果 | 元記録（保存先からの相対パス） |
| --- | --- | --- |
| 変更前、制御窓なし | 待機0秒の3/3成功。利用者の表示モードは未確定 | baseline-current-display/ |
| 変更前、fullscreen開始2組 | 各組2/3成功、各初回は失敗。独立したfullscreen開始は**0/2成功** | fullscreen-baseline-01/、-02/ |
| 表示をInitまで遅延、fullscreen開始1組 | 2/3成功、初回失敗。表示順だけでは解消せず | fullscreen-deferred-show-01/ |
| 表示遅延＋一時MoveToActiveSpace、1組 | 3/3成功 | fullscreen-move-to-active-01/ |
| 一時MoveToActiveSpaceのみ、2組 | 6/6成功。独立したfullscreen開始は2/2成功 | fullscreen-move-only-01/、-02/ |
| 復元・記録を整えた最終実装、fullscreen開始3組 | **9/9成功、独立したfullscreen開始3/3成功** | final-fullscreen-01/〜-03/ |
| 最終実装、通常/import前5秒/起動フック5秒を各3回 | **9/9成功**。自作fullscreen制御窓は使わず、通常サイズの検証窓で実行 | final-delays/ |

変更前と表示遅延だけの失敗は、scheduledでDOM hidden、frame1/frame2・shell・Python生成なし、active Space外・Visible遮蔽ビットなし、UIの10秒期限到達だった。20秒の外部watchdogが自分のホストを終了し、成功した再試行で原ログを置き換えていない。Windowsの以前の失敗と同因とは判断しない。

## 実装範囲と終了・期限の確認

Mac専用の`native/src/startup_macos.rs`で、主窓生成直後、taoの起動activateより前に元のcollectionBehaviorを保存し、CanJoinAllSpacesを除いてMoveToActiveSpaceを一時設定する。新しい設定・公開RPCは追加しない。最初の二重rAF後のshell通知で元の値を復元してからPython生成へ進む。shell前に初期化失敗または終了した場合も復元する。保存値をtakeするため復元ログは重複しない。fullscreen・tiling・cycle等の別のフラグは保持する。

一時変更を`macos_initial_space_behavior`のaction=applied/restoredとして有限記録し、Macのui_window_stateにwindow_collection_behavior_bitsを追加した。backend_ready直前にもMacの窓状態を1件記録する。今回の実窓では元0→一時2→元0だった。shell直後にactive Space=false/Visible=trueの標本があるが、backend_ready時は18件すべてactive Space=true/Visible=trueだった。遷移中の単一標本だけを描画失敗や実提示の証明にしない。

| 追加確認 | 結果 |
| --- | --- |
| 上表の最終18起動の順序 | 全件scheduled/frame1/frame2→shell→設定復元→Python生成→worker6段階→welcome。両期限10,000msを維持、watchdogなし |
| 描画通知を送らないコピー | 約10,265msで従来のUI初期化期限に到達。Python生成なし、reason=failureで元の値へ復元。期待した障害DOMを診断okとして自動終了（正常起動の成功ではない） |
| shell前の終了 | webview_created確認後に自分のPopenへSIGTERM。Python生成なし、reason=shutdownで復元、終了コード0 |
| 上記2ケースの回収 | watchdogなし、観測した自分のプロセスに残留なし |

SDKの二重rAF・接続10秒・公開ready、ホストのUI/worker各10秒、Python開始順は変更していない。Windowsの処理はcfgで対象外。検証窓の動きとメッセージを記録したもので、物理的な画面提示時間・NFRの性能値ではない。

## 検査・生成.app

SDK 26.5をコマンド単位で指定し、GUI試験と重ねずに実行した。Rust **14成功**（lib 9 + main 5、ignore/失敗0）、Clippy all-targetsの-D warnings、release build、fmt --checkに成功。Pythonは非推奨警告をerror指定して**145成功・スキップ0**。Chromium仮ネイティブのbrowser_native_bridgeも成功し、二重rAFと期限・RPC等の既存契約を確認した。これはWindows実WebView2検証ではない。NSWindowポインタの型・非null・Taoの所有期間・借用寿命・メインスレッドの前提を確認し、SAFETYコメントを付けた。

今回のホスト/wheel、前回ハッシュ照合したstandalone PythonとMac用wheelから、保存先の新しい`dist/ADR017 PyYaki.app`を生成した。生成前後に通常ファイル2,508件・symlink 8件の全manifest一致、pyc 0、アドホック署名のcodesign deep/strict検証に成功。自作fullscreen窓からの同梱Pythonスモークは変更前.app・更新.appとも1/1成功し、RPC・イベント/解除・取消と自動終了、観測worker回収を確認した。**旧.appの失敗は今回未再現であり、.appの失敗解消を示す比較ではない。** 更新.appは設定復元も照合し、起動後の全manifest・署名が一致した。

新しい.appのホストは署名後SHA-256 `717b0babadd482e1064d176731b30431ecedd234f9216f3d842e0cbaf49f58d1`。署名前のrelease入力のハッシュと区別する。正式署名・公証・他Mac・再配置試験の追加実施ではない。

## 保存物・未確認範囲

保存先に全trialソース/バイナリ、fullscreen制御fixtureとbefore-child/control-result、startup_probeの全host/worker/launcherログ、checks/、environment.json、final-evidence.json、生成wheel/.app/manifest、no-marker/early-shutdown/、bundle-comparison.json、bundle-before/after-fullscreen.jsonを保持した。各trialのreportにあるGit HEADだけでは検証用コピーの差分を表せないため、コピーソース・ビルドログ・バイナリハッシュも併せて照合する。

最終の読み取り照合では、追加の観測器で記録した自分の6 PIDに開始時刻一致の残留はなく、自作fullscreen制御プロセスも残っていなかった（final-process-check.json）。startup_probeのworkerは追加のプロセス同一性監視をしておらず、全WebKit XPCや未観測の子へ一般化しない。

自動承認レビューは、最初に組み込んだ製品側cargo fmtを、先行依頼の「検証用変更はコピー限定」に抵触するとして実行前に拒否した。製品対応とコピーでの比較を区別し、自動整形を外して読み取り専用fmt --checkと検査を実行した。書式差分は今回追加した2箇所だけを修正して再確認した。製品変更と生成物を混在させず、製品コミットは9e9302a、結果・現在地の整合更新は別の文書コミットへ分ける。

未確認・次の候補は、利用者のCodex fullscreenからの直接比較、利用者によるSpace移動/復帰・最小化・非表示が初期描画をまたぐ場合、複数ディスプレイ・Stage Manager・別macOS、目視と実close、Windows実機の回帰。非表示のまま10秒を超えた起動を成功へ変える契約変更は行っていない。必要ならADR-017の表示待ち/期限分離を別途検討する。新しい対策を全条件の解消やMVP全体の採択にはしない。
