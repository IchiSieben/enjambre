# Registers the visor builder as a Scheduled Task: every 5 minutes while this user is logged on.
# Run in Windows PowerShell from the enjambre folder:  powershell -File tools\visor\install-visor-task.ps1
# Idempotent: re-running replaces the task. Remove with:
#   Unregister-ScheduledTask -TaskPath '\enjambre\' -TaskName 'visor' -Confirm:$false
#
# WHY these settings:
#   - Interactive logon, limited rights: build.py reads this user's ~/.ssh/config and key to
#     `ssh mac cat` the mac's snapshot; it needs no admin and opens no port.
#   - A repeating trigger, not a resident loop: each run is short (~1-4 s) and stateless apart
#     from the collector's cursor file, so a crash costs one tick, not the service.
#   - ExecutionTimeLimit 2 min: a hung ssh must not pile up runs (IgnoreNew drops the overlap).
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pyw = Join-Path (Split-Path (Get-Command python).Source) 'pythonw.exe'
if (-not (Test-Path $pyw)) { throw "pythonw.exe not found next to python.exe ($pyw)" }
$user = "$env:USERDOMAIN\$env:USERNAME"

$action    = New-ScheduledTaskAction -Execute $pyw -Argument "`"$repo\tools\visor\build.py`"" -WorkingDirectory $repo
$trigger   = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
               -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
               -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskPath '\enjambre\' -TaskName 'visor' -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal -Description 'enjambre: visor snapshot + statusline summary (tools/visor/build.py)' -Force | Out-Null
Start-ScheduledTask -TaskPath '\enjambre\' -TaskName 'visor'
Get-ScheduledTask -TaskPath '\enjambre\' -TaskName 'visor' | Select-Object TaskPath, TaskName, State
