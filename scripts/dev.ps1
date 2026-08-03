[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"

function Resolve-Executable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string[]]$Fallbacks = @()
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    foreach ($fallback in $Fallbacks) {
        if (Test-Path -LiteralPath $fallback) {
            return $fallback
        }
    }

    throw "$Name bulunamadı. PATH'e ekleyin veya aracı kurun."
}

$uvFallbacks = @(
    (Join-Path $env:USERPROFILE "AppData\Local\uv\uv.exe")
)
$pythonScripts = Join-Path $env:APPDATA "Python"
if (Test-Path -LiteralPath $pythonScripts) {
    $uvFallbacks += Get-ChildItem -Path $pythonScripts -Filter "uv.exe" -File -Recurse -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
}
$uvPath = Resolve-Executable -Name "uv" -Fallbacks $uvFallbacks

$bunPath = Resolve-Executable -Name "bun" -Fallbacks @(
    (Join-Path $env:USERPROFILE ".bun\bin\bun.exe")
)

if (-not (Test-Path -LiteralPath $BackendDir)) {
    throw "Backend klasörü bulunamadı: $BackendDir"
}

if (-not (Test-Path -LiteralPath $FrontendDir)) {
    throw "Frontend klasörü bulunamadı: $FrontendDir"
}

$backendJob = Start-Job -Name "dortgoz-backend" -ArgumentList $BackendDir, $uvPath -ScriptBlock {
    param($WorkingDirectory, $UvExecutable)

    Set-Location $WorkingDirectory
    $env:DORTGOZ_MOCK = "1"
    & $UvExecutable run uvicorn dortgoz.main:app --reload --port 8000
}

$frontendJob = Start-Job -Name "dortgoz-frontend" -ArgumentList $FrontendDir, $bunPath -ScriptBlock {
    param($WorkingDirectory, $BunExecutable)

    Set-Location $WorkingDirectory
    & $BunExecutable run dev
}

$jobs = @($backendJob, $frontendJob)

Write-Host "Dörtgöz geliştirme sunucuları başlatıldı." -ForegroundColor Green
Write-Host "Backend:  http://localhost:8000/health"
Write-Host "Frontend: http://localhost:5173"
Write-Host "Durdurmak için Ctrl+C kullanın."

try {
    while ($true) {
        foreach ($job in $jobs) {
            Receive-Job -Job $job -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Host "[$($job.Name)] $_"
            }

            if ($job.State -in @("Failed", "Stopped", "Completed")) {
                $reason = if ($job.ChildJobs[0].JobStateInfo.Reason) {
                    $job.ChildJobs[0].JobStateInfo.Reason.Message
                } else {
                    "durum: $($job.State)"
                }
                Write-Warning "$($job.Name) sonlandı ($reason)."
            }
        }

        Start-Sleep -Milliseconds 250
    }
}
finally {
    foreach ($job in $jobs) {
        if ($job.State -eq "Running") {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
        }
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}
