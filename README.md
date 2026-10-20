# hwmonitor-mqtt

[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MQTT](https://img.shields.io/badge/MQTT-Realtime-FF6F00)](https://mqtt.org/)
[![License](https://img.shields.io/badge/License-GPLv3-2ea44f)](./LICENSE)

收集 Windows／Linux 電腦的 CPU、GPU、記憶體、磁碟、網路與溫度資料，透過 MQTT 發送，供網頁、終端介面、小螢幕或自訂程式即時顯示。可用資料取決於主機硬體、驅動與感測器權限。

Collect Windows/Linux hardware metrics, publish them over MQTT, and display them in web dashboards, terminal viewers or custom MQTT clients.

## 一張圖看懂用途

![電腦採集硬體狀態，透過 MQTT 同時傳給網頁與掌機顯示端](docs/assets/monitoring-overview.png)

## 複製給 AI，協助你安裝

把下面整段貼給**能讀取 GitHub 並執行本機終端指令的 AI 助理**，就能請它依環境協助安裝。你只需提供要監控的電腦與 MQTT 連線資訊；需要管理員權限或輸入帳密時，由你在本機完成。

```text
請幫我安裝 https://github.com/KarlSideProjects/hwmonitor-mqtt。
目標是收集這台電腦的硬體資料，透過 MQTT 顯示在網頁或其他接收端。
先讀 README、docs/getting-started/quickstart.md、docs/configuration/env-vars.md
與適用的部署文件，確認作業系統、現有安裝、可用感測器及 MQTT broker。
Windows 依 Windows sender 與排程安裝說明；Linux 先檢查 Docker profile
的硬體掛載及驅動需求，再選擇適用的部署方式，不要直接假設路徑存在。
保留既有設定；缺少主機或 broker 資訊時詢問我。帳密透過本機私有設定，
不要寫進 Git 或公開輸出。使用既有 broker；若我沒有 broker，先說明需要的
設定再協助建立。安裝依賴、啟動採集端，確認 MQTT 收到持續更新的資料，
再確認選定的顯示端看得到。最後告訴我如何開啟、停止與檢查服務，
並列出已通過的驗證和需要我實際確認的項目。
若我要使用 Linux 掌機面板，再讀同組織 mqtt-handheld-hardware-monitor
的 README 與設備清單，依機型選擇安裝方式。
```

## 專案分工與適用設備

本專案是通用的硬體監控資料採集與顯示工具，不限於特定掌機或顯示器。採集端、MQTT broker（訊息中繼服務）與顯示端可以部署在不同設備。

| 元件 | 用途與入口 |
|---|---|
| Linux 採集端 | 收集被監控主機的資料，見下方[快速開始](#快速開始) |
| Windows 採集端 | 在被監控電腦安裝 sender，見 [Windows 安裝](#windows-sender-安裝與注意事項) |
| MQTT broker | 轉送監控訊息，可使用既有 broker 或本專案的 Mosquitto 服務 |
| Web、TUI、Pygame 顯示端 | 本專案提供的網頁、終端與圖形介面 |
| 其他 MQTT 接收端 | 依本專案訊息格式整合自己的顯示器、設備或應用程式 |

### 同一組織下的相關專案

兩個專案都由 [KarlSideProjects](https://github.com/KarlSideProjects) 管理：

- **[hwmonitor-mqtt](https://github.com/KarlSideProjects/hwmonitor-mqtt)**：提供電腦資料採集、MQTT 傳送與通用 Viewer。
- **[mqtt-handheld-hardware-monitor](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor)**：把 Linux 掌機作為 MQTT 顯示面板，提供設備安裝腳本、實體按鍵操作與關閉／喚醒螢幕功能。目前已有 R36 MAX 2 與 TrimUI Smart Pro S 的安裝與驗證紀錄，見[硬體清單](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor#目前支援的硬體)及[實機操作圖解](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor/blob/main/docs/device-controls.md)。

這兩台掌機是顯示端的整合範例。其他設備需具備可用的 MQTT 接收程式，並依各自的作業系統、顯示方式及資料格式完成整合。

## 可以幫你做什麼

- **集中查看多台電腦：** 將各主機的負載與感測器資料送到同一個 MQTT broker，讓顯示端接收。
- **選擇適合自己的畫面：** 在網頁、終端或 Pygame 圖形介面查看資料，也能接上掌機面板。
- **沿用既有設備：** 已有 MQTT broker 或自訂顯示程式，可以依資料格式整合，採集端與顯示端分開部署。

## Viewer 預覽

### PyGame Viewer

![PyGame Viewer Preview](./docs/assets/previews/pygame-viewer-preview.svg)

### TUI Viewer

![TUI Viewer Preview](./docs/assets/previews/tui-viewer-preview.svg)

## 快速開始

若要監控 Windows，請依下方 [Windows Sender 安裝](#windows-sender-安裝與注意事項)操作。以下為 Docker 部署方式；掌機只作為顯示端時，請使用相關掌機專案的[安裝說明](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor/blob/main/docs/installation.md)。

### 1. 設定環境

```bash
cp .env.example .env
```

至少設定：`BROKER_HOST`、`BROKER_PORT`、`MQTT_USER`、`MQTT_PASS`。

### 2. 啟動模式

只啟動 Agent：

```bash
docker compose --profile agent up -d
```

完整模式（Agent + Broker + Web）：

```bash
docker compose --profile full up -d
```

供 ESP 等資源有限接收端使用的精簡資料模式（sender 仍在被監控主機執行）：

```bash
docker compose --profile esp-agent up -d
```

### 3. 觀察狀態

```bash
docker compose ps
docker compose logs -f
```

Web 介面預設：`http://localhost:8088`

## 文件入口

- [文件中心](./docs/README.md)
- [快速開始](./docs/getting-started/quickstart.md)
- [部署說明](./docs/deployment/docker-profiles.md)
- [環境變數](./docs/configuration/env-vars.md)
- [系統架構](./docs/architecture/system-overview.md)
- [硬體設定](./docs/hardware/README.md)
- [疑難排解](./docs/troubleshooting/common-issues.md)

## Python 開發

本專案有 `pyproject.toml`，建議使用 `uv`：

```bash
uv sync
uv run pytest -q
```

## 授權

本專案採用 `GNU GPL v3.0`。  
可商用，但散佈修改版或衍生作品時，需依 GPL 條款提供對應原始碼。詳見 [LICENSE](./LICENSE)。

## Windows Sender 安裝與注意事項

建議使用「系統管理員」PowerShell 執行以下步驟。

### 1. 準備 `.env`

```powershell
Copy-Item .env.example .env
```

請至少確認：
- `BROKER_HOST`
- `BROKER_PORT`
- `MQTT_USER`
- `MQTT_PASS`

### 2. 安裝 Windows sender 排程（建議開機自動啟動）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_windows_sender_task.ps1 -AtStartup
```

說明：
- 會優先使用 `uv`（若可用），並執行 `uv sync` 安裝依賴。
- 會自動檢查/安裝 LibreHardwareMonitor 到 `third_party/librehardwaremonitor`（預設啟用）。
- `-AtStartup` 會用 `SYSTEM` 建立排程，通常可避免 UAC 導致的 LHM 啟動問題。

### 3. 啟動與檢查

```powershell
Start-ScheduledTask -TaskName HWMonitorMQTTSender
Get-ScheduledTaskInfo -TaskName HWMonitorMQTTSender
```

### 4. 重要注意事項

- 不要同時啟動多個 sender 實例，否則 viewer 可能出現 CPU/GPU 資料跳動或 `NA` 閃爍。
- 若看到 `Access is denied (0x80070005)`，請改用系統管理員 PowerShell。
- 若看到 `WinError 740`，代表目前權限不足以啟動 LibreHardwareMonitor。
- Windows sender 會先嘗試 WMI；若 WMI 回傳空資料，會改用 `LibreHardwareMonitorLib.dll`（`pythonnet`）讀取感測器，並保留 `nvidia-smi` fallback。
- 安裝後通常不需要重開機。

### 5. 清理重複 sender（必要時）

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'hwmonitor_mqtt\.agents\.agent_sender_(windows|async)' -or $_.CommandLine -match 'start_windows_sender\.ps1' } |
  Select-Object ProcessId, Name, CommandLine

# 確認後再停止
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'hwmonitor_mqtt\.agents\.agent_sender_(windows|async)' -or $_.CommandLine -match 'start_windows_sender\.ps1' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
