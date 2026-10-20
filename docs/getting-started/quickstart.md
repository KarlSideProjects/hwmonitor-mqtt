# 快速開始

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

## 安裝目標預覽

在實際要安裝的主機執行以下命令，可預覽偵測到的 OS、系統類型、執行環境與安裝命令；`--dry-run` 不會安裝或啟動服務。

```bash
uv run --no-sync python -m hwmonitor_mqtt.platforms.install --dry-run
```

PVE Docker sender 使用已偵測的宿主證據、PVE 專用 Compose 設定及唯讀 API 配置。準備私有設定後執行 `sudo uv run --no-sync python -m hwmonitor_mqtt.platforms.install --deployment docker --pve-config /etc/hwmonitor-mqtt/pve.env --dry-run` 檢查，細節見 [Docker profiles](../deployment/docker-profiles.md)。

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
