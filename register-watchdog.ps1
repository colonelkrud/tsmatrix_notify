$scriptPath = 'C:\Users\hall8\Documents\Scripts\tsmatrix_notify\watchdog.ps1'

$action = New-ScheduledTaskAction `
  -Execute 'PowerShell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
  -RestartCount    999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -StartWhenAvailable

Register-ScheduledTask `
  -TaskName   'TSMatrixNotify' `
  -Action     $action `
  -Trigger    $trigger `
  -Settings   $settings `
  -User       'SYSTEM' `
  -RunLevel   Highest
