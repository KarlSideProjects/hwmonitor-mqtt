# PR 交付與來源安裝驗收

PR 使用功能範圍內的獨立審查與 GitHub 必要檢查交付。檢查清單由
[`scripts/github-required-checks.json`](../../scripts/github-required-checks.json)
定義；GitHub 上的實際設定仍須讀回確認。

## 審查範圍與額度

本專案以每個 PR 為一批審查，預設允許一次完整獨立審查，及最多兩次針對
已證實缺陷或缺少材料的複核。這是本專案的額度，取代通用技能預設的
一次初審與一次複核。每批開始時記錄比較基準、候選與已使用次數。

使用者授權實作、建立 PR 與合併時，已包含同一核准範圍內的測試、審查及
缺陷修正。額度內的例行複核由代理繼續執行；額度用完時保留未通過的檢查，
提出一次整體的範圍與成本選擇。新增需求、變更風險或使用者另訂預算時，
依其授權處理。切換 commit、重推或拆 feature 不重設同一批次的額度。

獨立審查者先讀直接變更、所屬流程、命名依賴契約與實際測試證據。
未變的上下文可沿用先前親自查核的內容；只有指紋傳遞失效，不表示必須
重新讀取全部傳遞依賴。發現交互作用不明時，再展開必要的來源範圍。
每個 feature 仍須有完整判定；雜湊、舊結果及作者自審不能取代獨立審查。

全域 `policy_inputs` 只收共同政策與檢查器。功能程式、測試與部署文件
放入其消費者的 feature-local inputs；改動政策本身仍需完整獨立審查。

## 等待與故障判讀

監看既有 Actions run 時使用 `scripts/watch_checks.py --help` 所列介面。
預設每 30 秒觀察，單次最多等待 55 秒，只回報狀態變化。pending 或暫時
API 錯誤保留同一個 run handle；成功、失敗及取消由遠端終態判定。

下載工具時的 504 與測試失敗分別處理。先讀取已結束 job 的私有診斷，再決定
是否重跑該 job；監看工具不會自行重跑、合併或公開原始日誌。原始日誌及
私有狀態檔不得加入 Git，也不得貼出帳密或完整環境變數。

## 驗收證據

來源安裝驗收沿用 `scripts/release-tests/` 的隔離環境。PR 必跑來源 bootstrap
整合測試；完整 Linux x64、ARM64 與 PVE VM 驗收由排程或人工 dispatch 執行。
完整 VM 測試消耗較多時間與下載量，無法使用硬體加速時可能更慢。
工具與輸入說明見 [來源安裝驗收工具](../../scripts/release-tests/README.md#source-installation)。

receipt 必須區分候選下載 fixture 與正式公開網址，並記錄來源 commit、架構、
首次安裝、重裝、設定保存、MQTT 收訊及清理結果。未完成、逾時或只測到部分
步驟不能標示通過。模擬驗證不證明實體感測器正確。

正常及失敗路徑都應關閉所建立的 VM／容器，只保留小型、經過遮罩的證據。
需要保留失敗磁碟時必須明確指定並報告位置；不能把大型磁碟放進公開 artifact。

## GitHub 合併保護

設定檔要求 Actions 必要檢查通過且分支與主線同步，禁止 force push 與刪除
主分支，管理員也受保護。GitHub 人工審查數量維持未設定；本專案的獨立
代理審查證據由 flow checker 驗證，兩者不能互相冒充。

管理設定以 [GitHub branch protection API](https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection)
套用，然後讀回 `/repos/KarlSideProjects/hwmonitor-mqtt/branches/master/protection`
比對必要檢查及 `strict`、`enforce_admins` 等欄位。一般 CI 使用唯讀 token，
不負責修改合併保護。設定檔存在本身不代表遠端已啟用。
