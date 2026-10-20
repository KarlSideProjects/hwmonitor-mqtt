# 環境變數

## MQTT 連線

- `BROKER_HOST`
- `BROKER_PORT`
- `MQTT_USER`
- `MQTT_PASS`

## 發送策略

- `SENDER_PROFILE`: `full` 或 `esp`
- `PUBLISH_INTERVAL_SEC`
- `ESP_MIN_PUBLISH_INTERVAL_SEC`
- `ESP_HEARTBEAT_INTERVAL_SEC`

## Web

- `WEB_PORT`

## 建議

- 生產環境請用強密碼並限制 Broker 網段。
- 先在單機驗證 topic，再擴展到多主機。

## Windows Sender 附加參數

- `LHM_PATH`: LibreHardwareMonitor.exe 路徑（可選）
- `LHM_AUTOSTART`: `1/0`，啟用 sender 自動啟動 LibreHardwareMonitor（預設 `1`）
- `LHM_STARTUP_TIMEOUT_SEC`: 等待 WMI namespace 就緒秒數（預設 `15`）
- `LHM_KILL_ON_EXIT`: `1/0`，sender 結束時是否關閉自己啟動的 LibreHardwareMonitor（預設 `1`）
- `SENDER_PROFILE`: `full` 或 `esp`（Windows sender 也支援）
- `PUBLISH_INTERVAL_SEC`: 發送間隔（秒）
- `ESP_MIN_PUBLISH_INTERVAL_SEC`: esp 模式最小發送間隔（秒）
- `ESP_HEARTBEAT_INTERVAL_SEC`: esp 模式心跳發送間隔（秒）
- `ESP_CPU_DELTA`, `ESP_RAM_DELTA`, `ESP_TEMP_DELTA`: esp 模式變化閾值
- `ESP_NET_DELTA_BYTES`, `ESP_DISK_DELTA_BYTES`, `ESP_GPU_DELTA`: esp 模式 I/O 與 GPU 閾值

## Proxmox VE sender

原生 PVE sender 使用獨立私有設定檔 `/etc/hwmonitor-mqtt/pve.env`，範例見 [pve.env.example](./pve.env.example)。設定需包含 HTTPS API URL、唯讀 token、可信 CA、穩定 cluster ID 與目標 node。scope 檢查要求有效的 `VM.Audit` 能涵蓋 `/vms`、`Sys.Audit` 能讀取 `/access` ACL，並確認每個明確列出的 `/vms` ACL 範圍也有有效 `VM.Audit`。第一版僅支援有效權限中所有 privilege 名稱均以 `.Audit` 結尾的 token；任何非 Audit 權限都會拒絕，即使其值為 `0`。這是目前支援範圍，不代表已驗證 PVE 全部唯讀權限分類。第一版也保守拒絕含 `/pool/...` ACL 的 token (`pool_scope_unsupported`)；pool 指派、缺少傳遞權限或 ACL 不完整時不發布 guest inventory，主機硬體仍持續發布。

PVE systemd service 由 systemd 讀取既有 `.env` 的 MQTT 設定，不覆寫該檔；sender 程式及依賴安裝到 root-owned `/opt/hwmonitor-mqtt-pve`，服務以 transient unprivileged identity 執行。重裝會重新部署程式並重啟服務。服務固定使用 full payload。API 輪詢每 10 秒執行一次，使用者可用 `PVE_TIMEOUT_SEC` 調整每次請求 timeout；值必須大於 0 且不超過 30 秒。CA 檔需可供 systemd service identity 讀取。

Docker PVE 部署使用相同私有設定與設定內的 CA 路徑，唯讀掛載到 sender 容器；不把 token 或 CA 寫入 image。從已辨識的 PVE 宿主選用 `--deployment docker`，安裝器會先驗證 API scope，再使用 PVE Compose profile。由容器內執行時，宿主類型不會從容器 OS 推斷，需明確指定 `--target-system pve`；若無法從宿主檢查另一個 sender 的狀態，安裝器會要求改在宿主執行。
