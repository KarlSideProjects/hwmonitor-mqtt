# 電腦採集端安裝與維護

[返回首頁](../../README.md) · [文件中心](../README.md) · [掌機顯示端安裝](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor/blob/main/docs/installation.md)

本文件說明 Windows／Linux／PVE 採集端的安裝、權限變更與維護。採集端透過 MQTT 傳送資料，掌機顯示端需使用同一個 broker 與相符的主題設定。

- [Linux／PVE 安裝](#在-linux-或-pve-安裝-sender)，script：[bootstrap.sh](../../bootstrap.sh)。
- [Windows 安裝](#windows-sender-安裝與注意事項)，script：[bootstrap.ps1](../../bootstrap.ps1)。

## 在 Linux 或 PVE 安裝 Sender

在 Debian、Ubuntu 或 Proxmox VE 主機的 root shell 執行以下公開安裝步驟。安裝器需要 `curl`、`jq`、`tar`、`sha256sum`、`runuser`、`od` 與 `flock`。一般 Linux 使用 Docker Compose v2，PVE 使用原生 systemd；已辨識且受支援的既有安裝會保留 `.env` 與 Docker 覆寫檔；未知目錄或有衝突的舊 PVE Docker 容器會停止安裝。ARM64 僅支援 64 位元 Linux（包含 Raspberry Pi）；32 位元系統不支援。PVE 原生 systemd 僅支援 x64。

```bash
(
scratch=$(mktemp -d) || exit 1
trap 'rm -rf "$scratch"' EXIT
cd "$scratch" || exit 1
release_tag=$(curl --fail --silent --show-error https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest | jq -er '.tag_name') &&
[[ "$release_tag" =~ ^v0\.1\.[0-9]+$ ]] &&
release_base="https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases/download/$release_tag" &&
curl --fail --location "$release_base/install.sh" --output install.sh &&
curl --fail --location "$release_base/install.sh.sha256" --output install.sh.sha256 &&
sha256sum --check install.sh.sha256 && bash ./install.sh
)
```

目前公開入口需要多行步驟；安裝腳本及校驗碼來自同一發布來源，無法抵禦該來源遭竄改。安裝入口確認單一公開版本，下載相同版本的校驗資料與編譯包，驗證後才更新安裝。下載或校驗失敗不會更新現有程式；啟動失敗時會嘗試回復先前的程式與服務。

PVE 安裝入口會先檢查舊的 `sys-agent` Docker 容器。容器存在時會停止，請先完成下方遷移，再執行安裝。它會更新下載目錄中的程式，保留既有 `.env` 與私有 PVE 設定。

PVE 本機的節點名稱與 CA 由 PVE 安裝模組準備。模組將本機 `/etc/pve/pve-root-ca.pem` 複製到服務可讀的 `/etc/hwmonitor-mqtt/pve-root-ca.pem`，預設 API URL 使用本機完整主機名稱；HTTPS 憑證仍需符合 URL。cluster ID 用於監控資料識別，並非 API 登入參數。各欄位來源與用途見 [PVE 設定參考](../configuration/env-vars.md#proxmox-ve-sender)。既有實際設定保留，範本中的已知範例值會依本機資料補正。

### 安裝檔會做什麼

下載命令執行公開的 `install.sh`，由它辨識系統並呼叫對應平台模組。一般 Linux 缺少 Docker 時，若系統套件來源提供 Docker Engine 與 Compose v2，安裝器會使用 APT 安裝；否則先自行安裝這兩項。它會下載或更新 `/opt/hwmonitor-mqtt` 內的程式，保留 MQTT `.env`、PVE 私有設定與 Docker 自訂覆寫檔。一般 Linux 使用 Docker Compose sender；PVE 使用原生 systemd sender。Windows 編譯版入口會驗證並安裝套件、準備 LibreHardwareMonitor 與開機排程；從原始碼執行 Windows task installer 時才需 Python/uv。

PVE 分支依序執行下列操作：

1. 檢查舊 `sys-agent` 容器；存在時停止安裝，請先完成下方遷移。
2. 準備本機節點、API URL、cluster ID 與服務可讀的 CA。
3. 沿用既有 token 設定；缺少或仍為範本密鑰時，透過本機 `pveum` 建立監控專用 PVE 帳號與 API token，不建立 Linux 帳號或登入密碼。
4. 將 token ID 與密鑰直接寫入 `/etc/hwmonitor-mqtt/pve.env`，權限為 `0600`，不在終端顯示密鑰。
5. 為新帳號與 token 在 `/` 設定可繼承的 `PVEAuditor` 唯讀角色。token 使用分離權限 (`privsep=1`)，不授予 VM／CT 的啟停或修改權限。
6. 驗證 CA、HTTPS API 與 token scope，再詢問缺少的 MQTT 設定。
7. 將編譯版 sender 安裝至 `/opt/hwmonitor-mqtt-pve`，修正執行檔與目錄的讀取、執行權限，啟用並重啟 `hwmonitor-mqtt-pve.service`，再檢查服務是否為 active。採集服務以非 root 的動態身分執行；MQTT 與 PVE 密鑰檔維持私有權限。

建立帳號、token 與 ACL 是 PVE 設定變更。安裝器為新安裝產生專用名稱，避免擴充其他帳號權限；不刪除或重設既有 token。既有自訂 token ID 缺少密鑰，或密鑰驗證失敗時會停止，不自動替換。只含安裝進度與專用識別碼的 `/etc/hwmonitor-mqtt/pve-token-state.json` 用於失敗重跑；實際密鑰只存在私有 `pve.env`。重裝沿用已保存的密鑰，接續未完成的權限設定。

自動建立 token 的流程已以模擬 `pveum` 測試，實際 PVE 帳號建立與重裝仍待宿主驗證。若要撤銷自動建立的存取權，先停用採集服務，再依狀態檔中的識別碼，在 PVE「Datacenter → Permissions → API Tokens」刪除該 token；專用帳號可在「Users」確認無其他用途後刪除。保留舊容器的回復步驟見下節。

安裝入口會檢查有效的 `VM.Audit`、`Sys.Audit` 與 ACL 範圍；目前有效權限名稱都必須以 `.Audit` 結尾，且可見的叢集 ACL 不可包含 `/pool/...` 項目。完整限制見 [PVE 設定參考](../configuration/env-vars.md#proxmox-ve-sender)。看到 `PVE sender is not installed yet` 代表本次尚未安裝完成。一般 Linux 與 Windows 不會建立 PVE 帳號或 token。

### PVE 服務啟動權限

若安裝完成後沒有監控資料，且服務日誌出現 `200/CHDIR` 或 `Permission denied`，可能是舊安裝器在 `umask 077` 下建立了服務無法讀取的程式目錄或 Python 環境。新版安裝器會修正這些執行檔案的權限，保留既有 MQTT 與 PVE token 設定；在 PVE root shell 重跑上方下載安裝步驟。

### PVE HTTPS 連線檢查

HTTPS 連線必須通過設定 CA 的信任鏈與主機名稱驗證。[Python 3.13 起的預設嚴格憑證檢查](https://docs.python.org/3/library/ssl.html#ssl.create_default_context)可能拒絕缺少 key usage 擴充的既有 PVE CA。採集端只在 OpenSSL 明確回報此原因 (驗證碼 `92`) 時，針對該 PVE 連線取消嚴格格式檢查並重試一次；CA 信任、憑證有效期及主機名稱驗證仍保留，不會改動 PVE 的憑證或全域 TLS 設定。

遇到此相容性問題時，在 PVE root shell 重新執行上方下載安裝步驟，無須重建 token。

### 從舊 Docker sender 遷移到 PVE 原生服務

以下遷移步驟尚待 PVE 實機重裝驗證。先退出監控安裝程序，再以 root 執行。此步驟會暫停舊監控；保留舊容器作為回復備份，不影響 CT／VM：

```bash
docker stop sys-agent && docker rename sys-agent sys-agent-before-native
```

重新執行上方下載安裝命令。停止但仍名為 `sys-agent` 的容器也會阻擋安裝；不要執行會停止其他服務的 `docker compose down`。若備份名稱已存在，先檢查既有備份，不要覆寫。

安裝後檢查服務與日誌，並在 R36 開啟 PVE 主機確認 CT／VM 清單：

```bash
systemctl status hwmonitor-mqtt-pve --no-pager
journalctl -u hwmonitor-mqtt-pve -n 30 --no-pager
```

若需回復保留的舊容器，先停用新版，再恢復舊容器名稱與啟動。若新服務尚未建立，略過第一個指令：

```bash
systemctl disable --now hwmonitor-mqtt-pve
docker rename sys-agent-before-native sys-agent && docker start sys-agent
```

此候選版支援 Linux x64 與 64 位元 ARM64，ARM64 安裝仍待驗證。PVE 原生安裝與 Windows 排程安裝尚未在實體目標主機驗證。Windows 請使用 [Windows sender 安裝方式](#windows-sender-安裝與注意事項)。

## Windows Sender 安裝與注意事項

在 64 位元「系統管理員」PowerShell 執行以下公開安裝步驟；首次執行會隱藏 MQTT 密碼輸入，並建立僅 Administrators 與 SYSTEM 可讀的 `.env`，已辨識且受支援的既有設定會保留；無法辨識的目錄會停止安裝。安裝器驗證編譯套件，準備 LibreHardwareMonitor 與 PawnIO，註冊開機排程並立即啟動 sender；從原始碼執行 task installer 的路徑才需 Python/uv。

```powershell
& {
$releaseTag = (Invoke-RestMethod 'https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest' -ErrorAction Stop).tag_name
if ($releaseTag -cnotmatch '^v0\.1\.[0-9]+$') { throw 'Unexpected release tag' }
$releaseBase = "https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases/download/$releaseTag"
$client = [Net.WebClient]::new()
try {
    $installer = $client.DownloadData("$releaseBase/install.ps1")
    $checksum = $client.DownloadData("$releaseBase/install.ps1.sha256")
} finally { $client.Dispose() }
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$expectedHash = (($utf8.GetString($checksum)).Trim() -split '\s+')[0]
$sha256 = [Security.Cryptography.SHA256]::Create()
try { $actualHash = ([BitConverter]::ToString($sha256.ComputeHash($installer))).Replace('-', '').ToLowerInvariant() }
finally { $sha256.Dispose() }
if ($expectedHash -cnotmatch '^[a-f0-9]{64}$' -or $actualHash -ne $expectedHash) { throw 'Installer checksum mismatch' }
& ([ScriptBlock]::Create($utf8.GetString($installer)))
}
```

檢查排程狀態：

```powershell
Get-ScheduledTaskInfo -TaskName HWMonitorMQTTSender
```

### 重要注意事項

- 不要同時啟動多個 sender 實例，否則 viewer 可能出現 CPU/GPU 資料跳動或 `NA` 閃爍。
- 若看到 `Access is denied (0x80070005)`，請改用系統管理員 PowerShell。
- 若看到 `WinError 740`，代表目前權限不足以啟動 LibreHardwareMonitor。
- Windows sender 以 `LibreHardwareMonitorLib.dll`（`pythonnet`）在程序內讀取 CPU/GPU 溫度。LibreHardwareMonitor 0.9.5 起移除 WMI provider，並改用 [PawnIO](https://pawnio.eu/) 驅動取代 WinRing0；未安裝 PawnIO 時 GPU 溫度仍可讀取，CPU 溫度會缺少。安裝器會靜默安裝固定版本與 SHA-256 的 PawnIO 2.2.0。
- 舊版 LibreHardwareMonitor 的 WMI namespace（`root/LibreHardwareMonitor`）與 `nvidia-smi` 仍作為 fallback。
- 安裝後通常不需要重開機；若安裝器顯示 PawnIO 需要重新開機，重開後 CPU 溫度才會出現。

### 清理重複 sender（必要時）

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'hwmonitor_mqtt\.agents\.agent_sender_(windows|async)' -or $_.CommandLine -match 'start_windows_sender\.ps1' } |
  Select-Object ProcessId, Name, CommandLine

# 確認後再停止
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'hwmonitor_mqtt\.agents\.agent_sender_(windows|async)' -or $_.CommandLine -match 'start_windows_sender\.ps1' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
