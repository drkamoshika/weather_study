# 個人用気象資料ダッシュボード（静的版）

気象庁などの資料を必要なときだけ収集し、日付・地点ごとの snapshot として保存して、GitHub Pages または任意の静的 HTTP サーバーで読む個人学習用ダッシュボードです。

以前の `server.py` + `/api/*` 方式は保全していますが、新しい画面は Python サーバーにも API にも依存しません。既存の `data/state.json` と `data/assets/` も変更・削除していません。

## Architecture

```text
GitHub Actions（毎日07:00 JST + 手動: date / location / sources / force）
  ├─ scripts/collect.py           公式資料を取得
  │    └─ data/snapshots/YYYY-MM-DD/location-id/
  │         ├─ manifest.json      v2: 取得区分、出典、時刻、hash、status、error
  │         ├─ data/*.json        AMeDAS、予報、公式テキスト
  │         └─ assets/*           天気図・航空気象図
  ├─ scripts/generate_llm_analysis.py
  │    ├─ llm-analysis.md         snapshot生成時の学習用解説
  │    └─ llm-analysis.json       モデル、生成時刻、入力資料、Markdownパス
  ├─ data/archive_index.json      全snapshotの取得履歴索引
  └─ scripts/build_static.py
       └─ dist/ → GitHub Pages
```

収集と閲覧は完全に分離されています。Pages のチェック欄は「保存済み資料の表示フィルター」で、新規取得ボタンではありません。

## 1. 既存データを移行する

旧データを読み取り、新形式へコピーします。元ファイルは変更しません。

```sh
cd /Users/yn/Documents/code/work/weather_study
python3 scripts/migrate_existing_data.py
```

結果には次の集計が表示されます。

- `snapshots`: 作成した snapshot 数
- `sources_migrated`: 移行できた資料数
- `duplicates`: すでに存在していて再利用した snapshot 数
- `metadata_incomplete`: 旧データに必要情報がなかった資料数
- `missing_references`: 参照先画像がなかった資料数

既存の移行 snapshot を作り直す場合だけ `--force` を付けます。この場合も旧 `state.json` と画像は上書きしません。

## 2. ローカルで保存済み snapshot を見る

```sh
python3 scripts/build_static.py
cd dist
python3 -m http.server 8000
```

ブラウザで `http://127.0.0.1:8000/` を開きます。`file://` で直接開くとブラウザの `fetch` 制限により JSON を読めないため、必ず静的 HTTP サーバーを使ってください。

日付、地点、資料種別、全選択・全解除、画像拡大・再クリックズーム、AMeDAS表・地図、予報、航空資料、公式実況テキスト、生成済み Gemini 解説を利用できます。`?date=2026-08-22&location=tokyo` のような URL から表示を復元できます。

「取得履歴」には日付、地点、自動取得／手動取得、保存資料数、Gemini解説の有無を表示します。各行を選ぶと、そのsnapshotを直接開けます。

## 3. ローカルで新規データを取得する

標準資料一式:

```sh
python3 scripts/collect.py \
  --date 2026-08-22 \
  --location tokyo \
  --sources standard
```

資料を限定する例:

```sh
python3 scripts/collect.py \
  --date 2026-08-22 \
  --location osaka \
  --sources weather-map,amedas,forecast,aviation:taf
```

- `standard`: 地上・高層・数値予報・AMeDAS・予報・解説・主要航空資料
- `all`: `config/sources.json` の全資料
- カンマ区切り: 指定した source ID だけ
- `--force`: 成功済みキャッシュも再取得。省略時は同じ日付・地点・sourceを再利用
- `--collection-mode manual|scheduled`: 取得区分。ローカル実行の既定値は `manual`

地点は `config/locations.json`、空港 ICAO は `config/airports.json`、資料は `config/sources.json` で管理します。TAF は `location → airport_icao → URL → 保存ファイル → manifest → 表示` が同じ地点になる構成です。全国共通の航空図は地点非依存です。

PDF は Linux/Actions でも使える Poppler の `pdftoppm` で PNG に変換します。

```sh
# macOS
brew install poppler

# Ubuntu
sudo apt-get install poppler-utils
```

一資料でエラーが起きても全体は止まりません。その source は `failed` または `unavailable` となり、理由が manifest と画面に残ります。

## 4. Gemini の事前生成

API キーは環境変数または GitHub Actions Secret だけで使用します。JavaScript、snapshot、`dist/` には入りません。
`GEMINI_API_KEY` は任意です。未設定の場合は解説生成を `skipped` として記録して正常終了し、snapshot の取得・保存・Pages 公開はそのまま続行します。

```sh
read -s GEMINI_API_KEY
export GEMINI_API_KEY
# GEMINI_MODELは任意。通常は未設定のまま利用可能モデルを自動選択
python3 scripts/generate_llm_analysis.py --date 2026-08-22 --location tokyo
python3 scripts/build_static.py
```

モデル方針は `config/gemini.json` で管理します。2026年8月時点の公式モデル一覧に合わせ、既定は `gemini-3.7-flash`、既知のfallbackは3.6/3.5系Flashです。ただし実行時にはAPIのモデル一覧を照合し、利用可能な新しい安定版Flashを優先します。Pro、画像生成、TTS、Live、廃止・非推奨モデルは候補にしません。`GEMINI_MODEL` はFlashモデルだけ上書き候補にできます。

生成結果は `llm-analysis.md` と `llm-analysis.json` の両方へ保存します。JSONには実際のモデル、生成時刻、入力に使ったsource ID、Markdownパスを記録します。解説には概況、気圧配置、前線等、850/500hPa、数値予報、航空気象、翌日の変化、不確実性の各節を必須にしています。画像入力が使えない場合は同じモデルのtext-only、さらに失敗した場合は次のFlashへフォールバックします。

API key未設定または生成失敗でも収集・commit・Pages公開は止まりません。manifestの `llm.status` を `skipped` / `failed` として残し、過去に保存済みのMarkdown/JSONを削除しません。

## 5. GitHub Actions と Pages

1. このディレクトリを GitHub リポジトリへ追加します。本作業では remote 作成、公開範囲の決定、push は行っていません。
2. Repository Settings → Secrets and variables → Actions で、必要なら Secret `GEMINI_API_KEY` と Variable `GEMINI_MODEL` を登録します。
3. Settings → Pages → Build and deployment の Source を **GitHub Actions** にします。
4. Actions → **Build weather snapshot** → Run workflow を開きます。
5. 次の入力を確認して実行します。

- `date`: `YYYY-MM-DD`。空欄なら日本時間の当日
- `location`: `tokyo`、`osaka` など地点 ID。空欄なら `tokyo`
- `sources`: `standard`、`all`、またはカンマ区切り ID。空欄なら `all`
- `force`: `true` なら既存成功資料も再取得

したがって入力を何も変更せず **Run workflow** を押すだけで、「日本時間の今日・東京・全資料」を取得します。

workflow は checkout、Python、Poppler、collector、Gemini、snapshotとarchive indexのcommit/push、static build、Pages artifact upload、deploy の順です。同時実行は直列化し、pushイベントでは起動しないためsnapshotのpushで再実行ループしません。変更がない場合はcommit/pushを省略します。`contents: write`、`pages: write`、`id-token: write` が必要です。

手動実行に加えて、毎日 **日本時間 7:00** に定期実行します。GitHub Actions の cron はUTCのため、workflowには `0 22 * * *`（前日22:00 UTC）を設定しています。定期実行時も「当日・東京・全資料」を取得し、`GEMINI_API_KEY` が設定済みなら同じrunの中でGemini解説を生成してからPagesを更新します。GitHub側の混雑により開始が遅れる場合があります。

2026-08-23 時点の GitHub 公式例に合わせ、`configure-pages@v5`、`upload-pages-artifact@v4`、`deploy-pages@v4` を使用しています。private repository の Pages 可否はプランに依存するため、公開範囲を決める際に最新仕様を確認してください。

## Web画面からの更新

GitHub Pagesの「新しい気象資料を取得」パネルから、次のCloudflare Workerを経由して既存の `Build weather snapshot` workflowを開始できます。

```text
https://weather-study-trigger.lvtm-pal.workers.dev
```

画面では対象日、地点、`all / standard / 現在チェック中の資料`、強制再取得を指定できます。ブラウザはWorkerへPOSTし、返された `run_id` を使って `/status` を約5秒ごとに確認します。実行中はボタンと入力を無効化し、完了後は取得した日付・地点のURLへ自動的に再読込します。

実行中の情報は `localStorage` の `weather_study_active_run_v1` に保存するため、途中でページを再読込しても同じActions runの追跡を再開します。保存するのは `run_id`、Actions URL、選択値、開始時刻だけです。GitHub Token、Gemini API keyなどのSecretはHTML、JavaScript、localStorageへ保存しません。

Cloudflare側ではWorkerのSecretとしてGitHub Tokenと対象repository情報を設定し、Pagesのoriginに対するCORSを許可してください。Workerは次の契約を実装する必要があります。

- `POST /`: `date`、`location`、`sources`、`force` を受け取りworkflow_dispatchし、`run_id` と `html_url` を返す
- `GET /status?run_id=...`: Actions runの `status`、`conclusion`、`html_url` を返す

Workerの作成・Secret設定はCloudflare GUIで行います。ブラウザからGitHub APIを直接呼ぶ実装にはしないでください。

## snapshot と manifest

```text
data/snapshots/2026-08-22/tokyo/
├── manifest.json
├── data/
│   ├── amedas.json
│   └── forecast.json
├── assets/
│   ├── weather-map.png
│   └── aviation-taf.png
├── llm-analysis.md
└── llm-analysis.json
```

manifest v2 は `schema_version`、対象日、`collection_mode`（`scheduled` / `manual`）、生成時刻、collector版、地点（緯度経度・空港）、要求 source、取得元 URL、local/data path、MIME type、取得・発表・有効時刻、SHA-256、cache hit、status、error、Gemini利用有無を持ちます。確実に判定できない時刻は `null` です。

`data/archive_index.json` はmanifestから再生成できる派生索引です。次のコマンドで安全に作り直せます。snapshot、画像、LLM成果物、manifestは削除しません。

```sh
python3 scripts/build_archive_index.py
```

旧manifestだけをv2へ更新する場合は `python3 scripts/upgrade_snapshot_metadata.py` を使います。資料ファイルやLLM成果物には触れません。

status:

- `success`: 取得・移行成功
- `cached`: 既存ファイル再利用
- `unavailable`: 公式側に対象日データなし
- `failed`: 通信・変換等の失敗

旧 AMeDAS の `temperature` と `wind` は実際には日最高気温・最大風速だったため、移行時に `temperature_max_c` と `wind_speed_max_m_s` へ改名しました。平均気温・平均風速とは表示しません。

## テスト

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build_static.py
rg '/api/' web dist/*.js dist/*.html
```

最後の `rg` が何も表示しなければ静的 frontend に `/api/*` 依存はありません。

## 制約と注意

- 公式サイトが過去履歴を公開していない資料は、今後 snapshot を蓄積して初めて過去閲覧できます。日付指定で当時の資料を遡って取得できるとは限りません。
- 実況図、予想図、航空資料、予報で時刻の意味は異なります。取得元から確実に判定できない発表・有効時刻は `null` とし、推測しません。
- 予報 JSON は気象庁由来の livedoor 天気互換 API を参考情報として利用します。防災判断には気象庁公式情報を確認してください。
- Leaflet 地図タイル、予報アイコン、外部リンクは閲覧時のネット接続が必要です。保存済み図と JSON は相対パスです。
- 民間予報サイトの画面は保存・再配布しません。
- 旧版は `python3 server.py` で起動できますが、新しい `dist/` には不要です。
- snapshot配下の履歴ファイルは自動削除しません。同じ日付・地点を再取得しても既存のLLM成果物は保持し、再生成前はmanifestを `stale` として扱います。

## 現状監査の記録

- 指定された `COMPLETE_HANDOVER.md` は存在しませんでした。添付された移行仕様と現行コードを基準に判断しました。
- 開始時は Git リポジトリではなく `.gitignore` もありませんでした。GitHub remote、public/private、push は設定していません。
- 旧資産 `server.py`、`public/`、`data/state.json`、`data/assets/` はすべて保持しています。
- 旧 `public/app.js` は `/api/dashboard`、`/api/refresh`、`/api/gemini` 等に依存していました。新 `web/app.js` は生成済み相対ファイルだけを読みます。
- `server.py` に同居していた HTTP配信、取得、state更新、PDF変換、Gemini を CLI と builder に分離しました。
- 秘密値らしい埋め込みは確認されませんでした。`GEMINI_API_KEY` という環境変数名の説明・参照だけがあります。
