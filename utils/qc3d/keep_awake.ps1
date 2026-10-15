# Keep the laptop and its screen awake while long project work runs (training, uploads, model runs).
# Does not change the power plan: it only tells Windows "in use" every 30 s; stop it to restore normal sleep.
#   powershell -ExecutionPolicy Bypass -File utils\qc3d\keep_awake.ps1 [-Hours 24]
param([double]$Hours = 24)

Add-Type -Name Power -Namespace Win32 -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);
'@
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
$flags = [uint32]"0x80000003"
$until = (Get-Date).AddHours($Hours)
Set-Content -Path "$env:TEMP\qc3d_keep_awake.pid" -Value $PID
while ((Get-Date) -lt $until -and (Test-Path "$env:TEMP\qc3d_keep_awake.pid")) {
    [void][Win32.Power]::SetThreadExecutionState($flags)
    Start-Sleep -Seconds 30
}
[void][Win32.Power]::SetThreadExecutionState([uint32]"0x80000000")
