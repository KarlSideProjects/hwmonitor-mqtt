# 快速開始

先選擇要安裝的元件：

- 監控 Linux 主機：依以下 Docker 步驟啟動採集端。
- 監控 Windows 主機：使用下方的 Windows sender 安裝方式。
- 只想顯示資料：使用本專案的 Web／TUI／Pygame Viewer；Linux 掌機可參考同組織的 [mqtt-handheld-hardware-monitor 安裝說明](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor/blob/main/docs/installation.md)。

採集端、broker 與顯示端可分別部署。R36 MAX 2 與 TrimUI Smart Pro S 是顯示端範例，本專案也可供其他符合資料格式的 MQTT 接收端使用。

## 1. 準備環境

- Docker
- Docker Compose（使用 `docker compose` 指令）

## 2. 設定 `.env`

```bash
cp .env.example .env
```

最少需設定：

- `BROKER_HOST`
- `BROKER_PORT`
- `MQTT_USER`
- `MQTT_PASS`

## 3. 啟動服務

只跑 Agent：

```bash
docker compose --profile agent up -d
```

完整模式（Agent + Broker + Web）：

```bash
docker compose --profile full up -d
```

## 4. 觀察資料

- Web UI：`http://localhost:8088`
- 日誌：`docker compose logs -f`

## Windows 自啟（非 Docker）

若你要在 Windows 主機常駐 sender，可用排程工作自啟：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_windows_sender_task.ps1
```

詳細參數與卸載方式請見：`docs/deployment/windows-task-scheduler.md`

## Windows 排程自啟（含 uv 與依賴自動安裝）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_windows_sender_task.ps1
```

詳細參數請見：`docs/deployment/windows-task-scheduler.md`
