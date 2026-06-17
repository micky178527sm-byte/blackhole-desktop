# blackhole-desktop

**English** | [日本語](#日本語)

A ray-traced black hole that floats over your **desktop** and **gravitationally
lenses it** — the real text/windows/video behind the hole bend and magnify in
real time, like the [ghostty-blackhole](https://github.com/s0xDk/ghostty-blackhole)
preview, but over the whole desktop instead of one terminal. Its size tracks
**Claude Code's context-window fill**. Works with the native macOS Terminal (or
any terminal) — no Ghostty, no terminal switch.

- The hole **lenses the live desktop**: ScreenCaptureKit grabs the screen behind
  the window and the shader bends it (Einstein ring, magnification, geodesics).
- Empty context → a tiny hole. As the context fills, it grows and drifts faster.
- It **auto-drifts** on a slow Lissajous path; you can **pin it** with `pos.sh`.
- It stays visible until you explicitly stop it. Claude Code can still update
  its size, but an idle statusLine no longer makes it disappear.

100% local and free: PyObjC + native OpenGL + ScreenCaptureKit. No Apple
Developer account, no paid services. Nothing is recorded to disk — each frame is
read, lensed, and discarded; memory stays at a few tens of MB.

## Quick start

One line — clone and launch (macOS):

```bash
git clone https://github.com/micky178527sm-byte/blackhole-desktop.git ~/blackhole-desktop && ~/blackhole-desktop/run.sh
```

The first run installs the Python deps automatically and prompts for **Screen
Recording** permission (System Settings ▸ Privacy & Security ▸ Screen Recording)
— grant it, then run the command once more. Stop anytime with
`~/blackhole-desktop/stop.sh`. Open the settings (right-click the hole ▸ 設定)
to tune the disk, jets, lensing and more.

## Requirements

- macOS, Python 3 with: `PyOpenGL`, `pyobjc-framework-{Cocoa,Quartz,CoreMedia,ScreenCaptureKit}`.
- **Screen Recording permission** (System Settings → Privacy & Security → Screen
  Recording). The first run prompts for it; grant it and relaunch. This is what
  lets the hole see the desktop behind it. Nothing is saved.

## How it works

- `build.py` ports `blackhole.glsl` (a Ghostty custom shader) to a native GLSL
  fragment (`frag_capture.glsl`): same physics, but `iChannel0` is the **live
  desktop capture**. The shadow stays black, while the surrounding ring and
  disk-like band are built from extra warped samples of the desktop itself, so
  text/images look pulled into orbit instead of painted on as fixed light.
- `overlay_gl.py` is a transparent, click-through, always-on-top, all-Spaces
  window with a native `NSOpenGLView`. It screen-captures the display (excluding
  its own window, so no feedback), uploads each frame to a GL texture, and runs
  the shader. It reads the live level + position from two files and only hides
  when the level is explicitly set to `-1`.
- `level.py` is wired into Claude Code (statusLine + SessionStart/SessionEnd) and
  writes the context fill to `~/.claude/blackhole-level`. On the statusLine path
  it **relays your existing statusline unchanged** — the hole is purely additive.

State files the overlay polls:

| File | Meaning |
|------|---------|
| `~/.claude/blackhole-level` | `0..1` context fill, or `-1` = hidden (capture off). Written by `level.py`, and preserved until explicitly changed. |
| `~/.claude/blackhole-pos`   | `auto` (drift) or `X Y` (uv 0..1, top-left origin) to pin. Written by `pos.sh`. |

## Run

```sh
~/blackhole-desktop/run.sh      # build + launch (singleton)
~/blackhole-desktop/stop.sh     # stop
```

Finder launchers:

- Double-click `Start Blackhole.command` to start/restart the LaunchAgent and
  make the hole visible.
- Double-click `Stop Blackhole.command` to unload the LaunchAgent and stop the
  overlay.

Auto-start at login (optional):

```sh
launchctl load   ~/Library/LaunchAgents/com.blackhole.desktop.plist   # enable
launchctl unload ~/Library/LaunchAgents/com.blackhole.desktop.plist   # disable
```
(When launched by `launchd` the Screen Recording permission is attached to the
`python3` binary — if the hole renders but the desktop doesn't bend, grant that
binary Screen Recording in System Settings and reload the agent.)

## Move it

By default the hole **drifts slowly across the whole screen**. You can also:

- **Drag it** with the mouse — grab the hole and drop it where you want. It pins
  there. (The window is click-through everywhere except over the hole, so it
  never blocks the rest of the desktop.)
- **Double-click the hole** to release it back to automatic drift.
- **Right-click the hole** for a menu: Settings…, resume auto-drift, center, quit.

The **Settings** window has live sliders — Drift Speed, Disk Spin, Size, Tilt
(edge-on↔face-on), Brightness, Contrast, Light Color, Lens Reach, and a
Multi-Display toggle — plus **Save / Reset all / Cancel** buttons, and an
**Auto / 日本語 / English** language switcher. Changes preview live; Cancel (or
closing) reverts to the values from when the window opened. Saved settings persist
in `~/.claude/blackhole-config.json` across restarts. The whole settings panel is
**bilingual (English / Japanese)** and follows the system language on Auto.

Or from the shell:

```sh
~/blackhole-desktop/pos.sh auto        # resume automatic full-screen drift (default)
~/blackhole-desktop/pos.sh center      # pin to screen center
~/blackhole-desktop/pos.sh 0.85 0.2    # pin to a uv point (x: 0=left..1=right,
                                       #                     y: 0=top..1=bottom)
```

## Claude Code wiring (token mode)

Add to `~/.claude/settings.json` (the statusLine command relays your existing
`statusline.py`, so your status bar is unchanged):

```json
{
  "statusLine": { "type": "command", "command": "$HOME/blackhole-desktop/level.py" },
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "$HOME/blackhole-desktop/level.py" }] }],
    "SessionEnd":   [{ "hooks": [{ "type": "command", "command": "$HOME/blackhole-desktop/level.py" }] }]
  }
}
```

Test without a session: `echo 0.6 > ~/.claude/blackhole-level` (and `-1` to hide).
By default, `SessionEnd` keeps the last level so the hole remains visible. Set
`BH_HIDE_ON_SESSION_END=1` on the hook command if you want the old behavior.

## Tuning

Size and look live at the top of `build.py` (`HOLE_RADIUS`, `TOKEN_AREA_*`,
`TOKEN_EASE`, `DISK_OUTER`) and the rest in `blackhole.glsl`. Capture resolution
is `BH_CAP_SCALE` (default 0.5 of the display — lower = cheaper). Edit, then
re-run `run.sh`.

- `KEP_DIFF` (in `blackhole.glsl`) — accretion-disk streak rotation: `0` = rigid
  spin (cleanest, no moiré), `1` = full Keplerian (inner edge faster, but the
  spiral winds up into moiré over time). Default `0.08`. Only the disk light is
  affected; the background lensing is never touched.

Debug flags: `BH_PASSTHROUGH=1` blits the raw capture (pipeline test).

## License

The shader (`blackhole.glsl`) is MIT, from s0xDk/ghostty-blackhole. The desktop
port glue here follows the same spirit.

---

# 日本語

[English](#blackhole-desktop) | **日本語**

**デスクトップの上に浮かぶ**、レイトレースのブラックホールです。穴の背後にある
本物の文字・ウィンドウ・動画を、**重力レンズ**でリアルタイムに歪ませて拡大します。
[ghostty-blackhole](https://github.com/s0xDk/ghostty-blackhole) のプレビューに似て
いますが、ひとつのターミナルの中ではなく**デスクトップ全体**に効きます。穴の大きさは
**Claude Code のコンテキスト消費量**に追従します。macOS 純正ターミナル（や任意の
ターミナル）で動作し、Ghostty もターミナルの乗り換えも不要です。

- 穴は**ライブのデスクトップをレンズします**。ScreenCaptureKit がウィンドウ背後の
  画面を取得し、シェーダーが歪ませます（アインシュタインリング・拡大・測地線）。
- コンテキストが空 → 小さな穴。埋まるにつれて大きくなり、漂う速度も上がります。
- ゆっくりしたリサージュ曲線で**自動的に漂い**ます。`pos.sh` で**固定**もできます。
- 明示的に止めるまで表示され続けます。Claude Code は大きさを更新できますが、
  statusLine がアイドルになっても消えなくなりました。

100% ローカル・無料：PyObjC ＋ ネイティブ OpenGL ＋ ScreenCaptureKit。Apple
Developer 登録も有料サービスも不要。ディスクには何も記録しません。各フレームは
読み取り・レンズ処理して破棄するだけで、メモリ使用量は数十 MB に収まります。

## クイックスタート

ワンライン — クローンして起動（macOS）：

```bash
git clone https://github.com/micky178527sm-byte/blackhole-desktop.git ~/blackhole-desktop && ~/blackhole-desktop/run.sh
```

初回起動で Python の依存を自動インストールし、**画面収録**の許可（システム設定 ▸
プライバシーとセキュリティ ▸ 画面収録）を求められます。許可してからもう一度この
コマンドを実行してください。停止は `~/blackhole-desktop/stop.sh`。穴を右クリック ▸
設定 から円盤・ジェット・レンズなどを調整できます。

## 必要なもの

- macOS、Python 3（`PyOpenGL`、`pyobjc-framework-{Cocoa,Quartz,CoreMedia,ScreenCaptureKit}`）。
- **画面収録の許可**（システム設定 → プライバシーとセキュリティ → 画面収録）。
  初回起動で求められるので許可して再起動してください。これが穴に背後のデスクトップを
  見せる仕組みです。何も保存はしません。

## 仕組み

- `build.py` が `blackhole.glsl`（Ghostty のカスタムシェーダー）をネイティブ GLSL
  フラグメント（`frag_capture.glsl`）へ移植します。物理は同じですが、`iChannel0` が
  **ライブのデスクトップキャプチャ**になります。影は黒のまま、周囲のリングと円盤状の
  帯は、デスクトップ自身を歪めて追加サンプリングして作るので、文字や画像が固定の光
  として描かれるのではなく軌道に引き込まれて見えます。
- `overlay_gl.py` は、透明・クリックスルー・最前面・全 Space に出る `NSOpenGLView`
  のウィンドウです。ディスプレイをキャプチャし（自分のウィンドウは除外＝フィード
  バックなし）、各フレームを GL テクスチャに転送してシェーダーを走らせます。レベルと
  位置を2つのファイルから読み、レベルが明示的に `-1` のときだけ隠れます。
- `level.py` は Claude Code に組み込まれ（statusLine ＋ SessionStart/SessionEnd）、
  コンテキスト消費量を `~/.claude/blackhole-level` に書き込みます。statusLine 経路では
  **既存の statusline をそのまま中継**するので、穴は純粋に上乗せされるだけです。

オーバーレイが監視する状態ファイル：

| ファイル | 意味 |
|------|------|
| `~/.claude/blackhole-level` | `0..1` のコンテキスト消費量、または `-1` ＝ 非表示（キャプチャ停止）。`level.py` が書き込み、明示的に変えるまで保持。 |
| `~/.claude/blackhole-pos`   | `auto`（漂流）または `X Y`（uv 0..1、左上原点）で固定。`pos.sh` が書き込み。 |

## 起動

```sh
~/blackhole-desktop/run.sh      # ビルド ＋ 起動（シングルトン）
~/blackhole-desktop/stop.sh     # 停止
```

Finder ランチャ：

- `Start Blackhole.command` をダブルクリックで LaunchAgent を起動／再起動し、穴を表示。
- `Stop Blackhole.command` をダブルクリックで LaunchAgent を解除しオーバーレイを停止。

ログイン時に自動起動（任意）：

```sh
launchctl load   ~/Library/LaunchAgents/com.blackhole.desktop.plist   # 有効化
launchctl unload ~/Library/LaunchAgents/com.blackhole.desktop.plist   # 無効化
```
（`launchd` から起動した場合、画面収録の許可は `python3` バイナリに紐づきます。穴は
描画されるのにデスクトップが歪まないときは、そのバイナリにシステム設定で画面収録を
許可してエージェントを再読み込みしてください。）

## 動かす

既定では穴は**画面全体をゆっくり漂い**ます。さらに次のことができます：

- マウスで**ドラッグ**：穴をつかんで好きな場所に置くと、そこに固定されます。
  （ウィンドウは穴の上以外はクリックスルーなので、他の操作を邪魔しません。）
- 穴を**ダブルクリック**：自動漂流に戻します。
- 穴を**右クリック**：メニュー（設定…／自動巡回に戻す／中央に配置／終了）。

**設定**ウィンドウにはライブのスライダーがあります。移動速度・回転速度・大きさ・
傾き（真横↔正面）・光の強さ・くっきり感・光の色・レンズ範囲、そして拡張ディスプレイ
の切替に加え、**保存／設定を初期化／キャンセル**ボタンと、**自動／日本語／English**
の言語スイッチャーがあります。変更はその場でプレビューされ、キャンセル（または閉じる）
でウィンドウを開いた時点の値に戻ります。保存した設定は再起動をまたいで
`~/.claude/blackhole-config.json` に保持されます。設定パネル全体が**日英バイリンガル**で、
自動ではシステム言語に追従します。

シェルからも：

```sh
~/blackhole-desktop/pos.sh auto        # 全画面の自動漂流に戻す（既定）
~/blackhole-desktop/pos.sh center      # 画面中央に固定
~/blackhole-desktop/pos.sh 0.85 0.2    # uv 座標に固定（x: 0=左..1=右、
                                       #                y: 0=上..1=下）
```

## Claude Code への組み込み（トークンモード）

`~/.claude/settings.json` に追記します（statusLine コマンドは既存の `statusline.py` を
中継するので、ステータスバーの表示は変わりません）：

```json
{
  "statusLine": { "type": "command", "command": "$HOME/blackhole-desktop/level.py" },
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "$HOME/blackhole-desktop/level.py" }] }],
    "SessionEnd":   [{ "hooks": [{ "type": "command", "command": "$HOME/blackhole-desktop/level.py" }] }]
  }
}
```

セッションなしで試す：`echo 0.6 > ~/.claude/blackhole-level`（隠すには `-1`）。
既定では `SessionEnd` は最後のレベルを保持するので穴は表示され続けます。以前の挙動が
よければ、フックのコマンドに `BH_HIDE_ON_SESSION_END=1` を設定してください。

## 調整

大きさと見た目は `build.py` 冒頭（`HOLE_RADIUS`、`TOKEN_AREA_*`、`TOKEN_EASE`、
`DISK_OUTER`）と、残りは `blackhole.glsl` にあります。キャプチャ解像度は `BH_CAP_SCALE`
（既定はディスプレイの 0.5 倍。下げるほど軽い）。編集したら `run.sh` を再実行します。

- `KEP_DIFF`（`blackhole.glsl` 内）… 降着円盤の光の筋の回転：`0` ＝ 剛体回転（最も
  クリーン・モアレなし）、`1` ＝ 完全なケプラー回転（内側ほど速いが、時間とともに渦が
  巻き上がってモアレ化）。既定は `0.08`。影響するのは円盤の光だけで、背景の重力レンズ
  には一切触れません。

デバッグフラグ：`BH_PASSTHROUGH=1` で生のキャプチャを表示（パイプライン確認）。

## ライセンス

シェーダー（`blackhole.glsl`）は s0xDk/ghostty-blackhole 由来の MIT です。ここでの
デスクトップ移植の接着コードも同じ精神に従います。
