# Docker Profiles

## Profile 一覽

- `agent`: 系統監控發送端
- `pve`: Docker sender on a Proxmox VE host
- `esp-agent`: ESP 專用低頻率發送端
- `broker`: 內建 Mosquitto
- `web`: Web 監控介面
- `full`: 一次啟動完整組合

## 常用命令

```bash
# 查看 profile
docker compose config --profiles

# 啟動完整模式
docker compose --profile full up -d

# 看即時日誌
docker compose logs -f
```

## PVE 主機 sender

在 PVE 宿主機執行安裝器會先檢查宿主機，不會用 Python image 的 Debian 系統資訊代替宿主類型。先準備 `/etc/hwmonitor-mqtt/pve.env` 與 `/etc/hwmonitor-mqtt/pve-root-ca.pem`；設定格式沿用 [PVE 私有設定範例](../configuration/pve.env.example)，CA 路徑使用該宿主機路徑。設定檔與 CA 檔應限制為管理員可讀，不能放進 image 或 Git。安裝器會驗證 TLS、唯讀 token、node 與可見範圍，dry-run 不啟動服務：

```bash
sudo uv run --no-sync python -m hwmonitor_mqtt.platforms.install --deployment docker --pve-config /etc/hwmonitor-mqtt/pve.env --dry-run
sudo uv run --no-sync python -m hwmonitor_mqtt.platforms.install --deployment docker --pve-config /etc/hwmonitor-mqtt/pve.env
```

這會掛載私有設定及 CA 為唯讀，使用 host network、host PID namespace 和唯讀 `/sys`，不要求 `privileged`。`hostname` 取自宿主機，讓 sender 在重建後維持原本 MQTT 主機身分。容器仍使用自己的檔案系統；`/sys`、程序資訊與感測裝置只會呈現已掛載且容器可讀的內容，不能視為所有宿主硬體資料完整可見。PVE 客機清單則由 API 提供。

Docker 與原生 systemd sender 只能選一個。若原生 PVE service 已啟用或執行，Docker 安裝器會拒絕切換；先執行 `sudo systemctl disable --now hwmonitor-mqtt-pve.service`，再安裝 Docker sender。若 `sys-agent` 容器已存在，原生 PVE 安裝器也會拒絕切換；先用目前管理它的 Compose 專案停止並移除該容器。安裝器不會自動停止或刪除既有入口。Compose 固定使用 `sys-agent` 名稱，Docker daemon 也會拒絕同名 sender。

既有手動管理的容器應在宿主機使用 `--deployment docker`。若只能從既有容器取得資訊，`--target-system pve` 或 `linux` 可在 dry-run 中明確標記宿主目標；PVE 目標仍會驗證指定 API/CA。實際切換必須回到宿主執行安裝器，因為容器內無法可靠檢查宿主上的 systemd sender。容器發行版只記錄為容器 OS，不會作為宿主系統判定依據。PVE 與一般 Linux 各用固定 Compose 設定，不依賴 GPU 或 NVIDIA 裝置路徑。
