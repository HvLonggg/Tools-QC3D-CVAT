# Copyright (C) CVAT.ai Corporation
# SPDX-License-Identifier: MIT
# Run from any directory. Default: existing jobs, annotation, QC and AutoFix.
# -Mode data also starts task creation / import / export workers.
param([ValidateSet('light', 'data', 'full')][string]$Mode = 'light')

$ErrorActionPreference = 'Stop'
$qcRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$composeArgs = @('compose', '-f', (Join-Path $qcRepo 'docker-compose.yml'), '-f', (Join-Path $qcRepo 'docker-compose.qc3d.yml'))

& docker info --format '{{.ServerVersion}}' 2>$null
if ($LASTEXITCODE -ne 0) {
    $qcDockerDesktop = Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/Docker Desktop.exe'
    if (!(Test-Path -LiteralPath $qcDockerDesktop)) {
        throw 'Mở Docker Desktop trước, rồi chạy lại script.'
    }
    Start-Process -FilePath $qcDockerDesktop -WindowStyle Hidden
    $qcDeadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 3
        & docker info --format '{{.ServerVersion}}' 2>$null
        if ($LASTEXITCODE -eq 0) { break }
    } while ((Get-Date) -lt $qcDeadline)
    if ($LASTEXITCODE -ne 0) { throw 'Docker chưa sẵn sàng. Kiểm tra Docker Desktop rồi chạy lại.' }
}

# Stop only known optional CVAT services, preserving unrelated projects.
$qcOptional = @(
    'nuclio-nuclio-pth-facebookresearch-mask2former-cityscapes-panoptic', 'nuclio',
    'nuclio-local-storage-reader', 'cvat_worker_annotation', 'cvat_worker_webhooks',
    'cvat_worker_quality_reports', 'cvat_worker_consensus', 'cvat_grafana'
)
if ($Mode -eq 'light') { $qcOptional += @('cvat_worker_import', 'cvat_worker_export', 'cvat_worker_utils') }
if ($Mode -ne 'full') {
    $qcExisting = @(& docker ps -a --format '{{.Names}}')
    $qcToStop = @($qcOptional | Where-Object { $qcExisting -contains $_ })
    if ($qcToStop.Count) {
        & docker update --restart unless-stopped @qcToStop
        if ($LASTEXITCODE -ne 0) { throw 'Không cập nhật được trạng thái tự khởi động của dịch vụ phụ.' }
        & docker stop @qcToStop
        if ($LASTEXITCODE -ne 0) { throw 'Không dừng được dịch vụ phụ.' }
    }
}
if ($Mode -eq 'data') { $composeArgs += @('--profile', 'data', '--profile', 'maintenance') }
if ($Mode -eq 'full') { $composeArgs += @('--profile', 'full') }
& docker @composeArgs up -d --no-build
if ($LASTEXITCODE -ne 0) { throw 'Không khởi động được CVAT.' }
Write-Host "CVAT QC 3D ($Mode): http://localhost:8070/qc3d"
