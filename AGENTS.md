## Checks

`make check` 是 CI 執行的同一指令。PowerShell 測試透過 `tests/_pwsh.py` 的 `run_pwsh` 執行（`Stop` 模式；`PWSH_EXE` 可指定 `powershell.exe` 測 5.1）；CI 的 `windows-sender` job 會在 5.1 和 7 下各跑一次，並實際執行安裝器。

## Windows 溫度路徑

`bootstrap.ps1` → `scripts/windows/install_windows_sender_task.ps1`（固定版本與 SHA-256 的 LibreHardwareMonitor 與 PawnIO）→ 排程以 `-LhmPath <LibreHardwareMonitor.exe>` 執行 `start_windows_sender.ps1` → `hwmonitor_mqtt/agents/agent_sender_windows.py` 從 exe 同資料夾載入 `LibreHardwareMonitorLib.dll`。

改感測器解析前，先在實機執行 `python -m hwmonitor_mqtt.agents.agent_sender_windows --dump-sensors`，把輸出存成 `tests/fixtures/lhm-<機型>.json` 再寫測試。

請使用者在 Windows 測試未合併的版本時，給 commit SHA 網址，步驟見 `docs/troubleshooting/common-issues.md`。
