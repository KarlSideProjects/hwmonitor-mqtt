# 疑難排解

## Agent 沒有送資料

檢查：

```bash
docker compose logs -f sys_agent
```

確認：

- `.env` 的 Broker 參數正確
- Broker 可連通（IP/防火牆/Port）

## Web 沒有更新

檢查：

- `web_monitor` 是否啟動
- Browser Console 是否有 WebSocket 錯誤

## Windows 讀不到 CPU 溫度

- sender 必須以管理員或 `SYSTEM` 執行（`-AtStartup`）
- 日誌出現 `PawnIO driver is not installed` 時，重新執行安裝器，或手動安裝 [PawnIO](https://pawnio.eu/)
- LibreHardwareMonitor 0.9.5 起沒有 WMI Provider，sender 改從 `LibreHardwareMonitorLib.dll` 讀取；日誌應出現 `LibreHardwareMonitorLib collector is available.`
- sender 日誌第一段的 `Installed commit:` 顯示實際執行的版本
- 在專案目錄執行 `.\.venv\Scripts\python.exe -m hwmonitor_mqtt.agents.agent_sender_windows --dump-sensors`，可列出函式庫讀到的所有感測器

測試分支時，在系統管理員 PowerShell 先設定 `$env:HWMONITOR_REF='<branch>'` 再執行 bootstrap。`C:\ProgramData\hwmonitor-mqtt` 已存在時，bootstrap 會沿用既有程式碼並印出其 commit；要換版本請先移除該目錄。

## Windows Sender 自動啟動 LibreHardwareMonitor 失敗

只有 `LibreHardwareMonitorLib.dll` 無法載入時，sender 才會啟動 LHM 並等待舊版 WMI namespace。

檢查項目：

- `LHM_PATH` 是否指向有效的 `LibreHardwareMonitor.exe`
- 帳號是否有權限啟動外部程式
- 防毒/企業防護是否阻擋 LHM 啟動
- `LHM_STARTUP_TIMEOUT_SEC` 是否太短
- LHM 中是否啟用 WMI Provider（`root/LibreHardwareMonitor`）

建議：

- 先手動啟動 LHM 並確認 WMI namespace 可讀，再切回 `LHM_AUTOSTART=1`
- 若 sender 啟動了 LHM，預設會在 sender 結束時關閉（`LHM_KILL_ON_EXIT=1`）

## Windows GPU 仍為空值（WinError 740）

若日誌出現 `Failed to start LibreHardwareMonitor: [WinError 740]`，代表目前權限不足以啟動 LHM。

建議：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\uninstall_windows_sender_task.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_windows_sender_task.ps1 -AtStartup
```

`-AtStartup` 會以 `SYSTEM` 建立排程，通常可避免 UAC 導致的 LHM 啟動失敗。

另外，Windows sender 已加入 NVIDIA `nvidia-smi` fallback；若 LHM/WMI 暫不可用，仍可回報基本 GPU 指標。

## Windows sender: CPU/GPU intermittently becomes `NA`

If your viewer sometimes shows GPU data and then `NA`, or CPU temperature appears/disappears,
this is usually caused by partial sensor polls from LibreHardwareMonitor WMI.

Current sender behavior:
- Keeps last-known non-empty CPU temperature entries instead of clearing them on partial polls.
- Merges multi-GPU data by stable device id (`nvidia-0`, `nvidia-1`, etc.) to avoid dropping a secondary GPU when one poll is incomplete.
- Uses `nvidia-smi` fallback every cycle to refresh NVIDIA metrics when WMI is temporarily missing.

Notes:
- Reboot is usually not required after installing the task.
- If logs contain `WinError 740`, run sender task as `SYSTEM` (`-AtStartup`) or start LibreHardwareMonitor with elevated permission.
- Windows sender reads `LibreHardwareMonitorLib.dll` (via `pythonnet`) first and uses WMI only when the library returns nothing.
