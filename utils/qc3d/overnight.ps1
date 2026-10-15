# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT
#
# Unattended run: keep the laptop awake (screen on) without changing the power plan, wait for the
# PHENIKAA download, fill missing files, build + check the training set, then shut down in 10 minutes
# (cancel with:  shutdown /a ).
#
#   powershell -NoProfile -File utils\qc3d\overnight.ps1            # run, then shut down
#   powershell -NoProfile -File utils\qc3d\overnight.ps1 -NoShutdown

param(
    [switch]$NoShutdown,
    [int]$MaxWaitMinutes = 240
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$rclone = "$env:USERPROFILE\tools\rclone\rclone.exe"
$python = Join-Path $repo '.venv-qc3d\Scripts\python.exe'
$data = 'D:\dataset'
$log = Join-Path $data 'overnight.log'

function Log($text) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $text
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

# Ask Windows to keep the system and the display on while this script runs (reset when it exits).
Add-Type -Namespace Qc3d -Name Power -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);
'@
$ES_CONTINUOUS = [uint32]2147483648; $ES_SYSTEM_REQUIRED = [uint32]1; $ES_DISPLAY_REQUIRED = [uint32]2
[void][Qc3d.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_DISPLAY_REQUIRED)
Log "Bắt đầu. Máy và màn hình được giữ thức cho tới khi script kết thúc."

try {
    # 1) wait for the running download (started from Claude Code)
    $deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
    while ((Get-Process rclone -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        $count = (Get-ChildItem "$data\phenikaa_mini" -Recurse -File -ErrorAction SilentlyContinue).Count
        Log "Đang tải: $count file"
        Start-Sleep -Seconds 120
    }

    # 2) second pass: fetch anything missed (already downloaded files are skipped)
    Log 'Tải bù file còn thiếu'
    & $rclone copy gdrive: "$data\phenikaa_mini" --drive-root-folder-id 1G490BK62MlmYhMMlapE_Smm6JgO3WmnJ `
        --include '*/lidar/**' --include '*/label/**' --include '*/ego_pose/**' --include '*/Summary.txt' `
        --transfers 24 --checkers 32 --drive-pacer-min-sleep 10ms --drive-pacer-burst 200 `
        --retries 5 --low-level-retries 20 --log-file "$data\phenikaa_mini_download3.log" --log-level NOTICE
    Log ("rclone lượt 2 xong, mã thoát {0}; tổng {1} file" -f $LASTEXITCODE,
         (Get-ChildItem "$data\phenikaa_mini" -Recurse -File).Count)

    # 3) verify, build the training set, QC the PHENIKAA labels, zip for Kaggle
    Log 'Xử lý dữ liệu (kiểm tra, tạo bộ huấn luyện, QC nhãn, nén)'
    & $python (Join-Path $repo 'utils\qc3d\run_after_download.py') `
        --phenikaa "$data\phenikaa_mini" --nuscenes "$data\nuscenes" --out "$data\train_vn_v3" `
        --zip "$data\train_vn_kaggle_v3.zip" --report "$data\pipeline_report.md" --qc-stride 10 --qc-minutes 40 *>> $log
    Log "Xử lý xong, mã thoát $LASTEXITCODE. Báo cáo: $data\pipeline_report.md"
}
catch {
    Log "LỖI: $_"
}
finally {
    if (-not $NoShutdown) {
        Log 'Hẹn tắt máy sau 10 phút (huỷ bằng: shutdown /a)'
        shutdown.exe /s /t 600 /c "QC 3D: đã xử lý xong dữ liệu PHENIKAA. Máy tắt sau 10 phút. Huỷ: shutdown /a"
    }
    [void][Qc3d.Power]::SetThreadExecutionState($ES_CONTINUOUS)
    Log 'Kết thúc.'
}

