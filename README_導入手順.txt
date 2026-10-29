USDJPY 新方式 (Google Cloud / Apps Script / Blogger OAuth 不使用)

重要：旧auto-blogリポジトリには何も変更しません。旧ワークフローをOFFにしないでください。

1. GitHubに新規【Public】リポジトリを「usdjpy-live-display」という名前で作成。
   ※ Publicにするのは市場データ配信だけ。旧auto-blogはPrivateのまま維持。
2. ZIPを展開し、fx_generate.py / requirements.txt / docs/ / .github/workflows/
   を、新リポジトリの同じパスにアップロードしてCommit。
3. 新リポジトリ → Settings → Actions → General → Workflow permissions →
   Read and write permissions が許可されているか確認（許可されない場合は手動で設定）。
4. Actions → NEW USDJPY live display → Run workflow でまず手動実行。
   緑のチェックだけでなく、docs/latest.json に値と日時が入ったか確認する。
5. 新リポジトリ → Settings → Pages → Build and deployment:
   Source = Deploy from a branch / Branch = main / Folder = /docs → Save。
   https://hachimituuno-bit.github.io/usdjpy-live-display/ が表示されるか確認。
6. Bloggerの「新規のテスト下書き」のHTMLビューに、
   BLOGGER_貼り付け用.html の iframe タグだけ貼り、プレビューで表示確認。
   安全が確認できるまでは旧記事を編集・削除しない。
7. 既存記事を常にブログの上部に置きたい場合は、Bloggerのレイアウトで
   「注目の投稿(Featured Post)」ガジェットを追加して該当記事を選び、
   ホームページ上の上部に配置する。
   ※公開日順の記事一覧そのものの先頭に移すことは、この方法ではできません。

スケジュール：日本時間毎時03、18、33、48分（15分間隔）。
土曜 07:00〜日曜 06:59 は生成停止。日曜 07:00 再開。
日曜07:00以降と月〜金は実行。GitHub Actionsは遅延/欠落し得る。
表示HTMLは1分間隔でlatest.jsonを確認し、65分以上古ければ警告表示。
市場データが取れない場合は古いJSONを保持し、失敗をActions上で可視化。

旧版との差：日足/4時間足/1時間足 20点、15分足40点の計算を従来コードから移植。
「Bloggerの記事本文を15分ごとに書き換える」のではなく、記事中のiframe表示が
最新JSONを読み込む方式。Blogger投稿日時・公開日は変更しません。
専用のGitHub PublicリポジトリにPython分析コードが公開されます。
既存のGEMINI_API_KEYなどのSecretsは不要、追加しないこと。
