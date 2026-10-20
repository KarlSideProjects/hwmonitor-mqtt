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

原生 PVE sender 使用獨立私有設定檔 `/etc/hwmonitor-mqtt/pve.env`，範例見 [pve.env.example](./pve.env.example)。設定需包含 HTTPS API URL、唯讀 token、可信 CA、穩定 cluster ID 與目標 node。API scope 檢查要求 token 對 `/vms` 有可傳遞的 `VM.Audit`，並以 `Sys.Audit` 讀取 ACL 清單以確認沒有 guest 範圍的 `NoAccess` 覆寫。無此完整只讀範圍時不發布 guest inventory；主機硬體仍持續發布。

PVE systemd service 由 systemd 讀取既有 `.env` 的 MQTT 設定，不覆寫該檔；sender 程式及依賴安裝到 root-owned `/opt/hwmonitor-mqtt-pve`，服務以 transient unprivileged identity 執行。重裝會重新部署程式並重啟服務。服務固定使用 full payload。API 輪詢每 10 秒執行一次，使用者可用 `PVE_TIMEOUT_SEC` 調整每次請求 timeout（1–30 秒）。CA 檔需可供 systemd service identity 讀取。
