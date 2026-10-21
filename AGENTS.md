## Checks

`make check` 是 CI 執行的同一指令。`*.ps1` 語法測試需要本機 `pwsh`，缺少時會略過，只在 CI 真正執行。

## Windows 溫度路徑

`bootstrap.ps1` → `scripts/windows/install_windows_sender_task.ps1`（固定版本與 SHA-256 的 LibreHardwareMonitor 與 PawnIO）→ 排程以 `-LhmPath <LibreHardwareMonitor.exe>` 執行 `start_windows_sender.ps1` → `hwmonitor_mqtt/agents/agent_sender_windows.py` 從 exe 同資料夾載入 `LibreHardwareMonitorLib.dll`。

改感測器解析前，先在實機執行 `python -m hwmonitor_mqtt.agents.agent_sender_windows --dump-sensors`，把輸出存成 `tests/fixtures/lhm-<機型>.json` 再寫測試。
