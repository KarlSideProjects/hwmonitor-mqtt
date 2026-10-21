# hwmonitor-mqtt

在 Windows／Linux／PVE 電腦採集 CPU、GPU、記憶體、磁碟、網路與溫度，透過 MQTT 傳送監控資料。

**電腦採集端（本專案）** 負責採集及傳送；**[掌機顯示端 MQTT Handheld Hardware Monitor](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor)** 安裝在 R36 MAX 2／TrimUI Smart Pro S，負責顯示。兩邊需連到同一個 MQTT broker，並使用相符的主題設定。

電腦採集端 → MQTT broker → 掌機顯示端

## 安裝入口

| 要安裝在哪裡 | 安裝 script | 安裝步驟 |
| --- | --- | --- |
| Windows 電腦 | [bootstrap.ps1](bootstrap.ps1) | [系統管理員 PowerShell 命令](docs/deployment/installation.md#windows-sender-安裝與注意事項) |
| Linux／PVE 電腦 | [bootstrap.sh](bootstrap.sh) | [root Shell 命令](docs/deployment/installation.md#在-linux-或-pve-安裝-sender) |
| R36／TrimUI 掌機 | [掌機安裝入口](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor#安裝入口) | [掌機安裝步驟](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor/blob/main/docs/installation.md#快速安裝) |

首次安裝會詢問 MQTT 設定，既有設定會保留。PVE 使用原生 systemd 服務；Windows 使用開機排程。PVE 原生安裝與 Windows 排程安裝尚未在實體目標主機驗證，完整限制見 [安裝與維護](docs/deployment/installation.md)。

## 詳細文件

- [文件中心](docs/README.md)：所有說明入口。
- [安裝與維護](docs/deployment/installation.md)：平台安裝命令、PVE 遷移、Windows 溫度與排錯。
- [Docker 快速開始](docs/getting-started/quickstart.md)／[部署設定](docs/deployment/docker-profiles.md)：自行部署 broker、Web 與 sender。
- [環境變數](docs/configuration/env-vars.md)：MQTT 與 PVE 設定。
- [系統架構](docs/architecture/system-overview.md)／[硬體設定](docs/hardware/README.md)。
- [疑難排解](docs/troubleshooting/common-issues.md)／[Python 開發與驗證](docs/getting-started/development.md)。

程式碼採 [GPL v3](LICENSE)。
