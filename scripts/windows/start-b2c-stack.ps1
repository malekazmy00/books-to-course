# Start only the B2C Compose project after its dedicated disk is mounted.
$ErrorActionPreference = "Stop"
$distro = "B2C-On-D"
$logPath = Join-Path $env:ProgramData "B2C\start-b2c-stack.log"
New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
function Write-Log([string]$Message) {
    Add-Content -LiteralPath $logPath -Value ("{0:u} {1}" -f (Get-Date), $Message)
}
$deadline = (Get-Date).AddMinutes(15)
while ((Get-Date) -lt $deadline) {
    & wsl.exe -d $distro -- bash -lc "mountpoint -q /mnt/wsl/b2cdata && test -f /mnt/wsl/b2cdata/.b2c-data-disk && test -d /mnt/wsl/b2cdata/models"
    if ($LASTEXITCODE -eq 0) {
        & wsl.exe -d $distro -- bash -lc "docker info >/dev/null 2>&1"
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Verified B2C data mount and Docker daemon; starting only Compose project b2c."
            $savedPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $output = & wsl.exe -d $distro -- bash -lc "cd /root/projects/book2course && bash scripts/data-disk.sh check && docker compose -p b2c --env-file .env --profile speech --profile hy-translation -f docker/compose.yml -f docker/compose.observability.yml -f docker/compose.qa-local.yml -f docker/compose.speech.yml -f docker/compose.hy-translation.yml up -d --build && bash scripts/restore-model-services.sh && bash scripts/recover-lost-b2c-activities.sh && docker compose -p b2c --env-file .env --profile speech --profile hy-translation -f docker/compose.yml -f docker/compose.observability.yml -f docker/compose.qa-local.yml -f docker/compose.speech.yml -f docker/compose.hy-translation.yml restart cadvisor" 2>&1
            $result = $LASTEXITCODE
            $ErrorActionPreference = $savedPreference
            foreach ($line in $output) { Write-Log ([string]$line) }
            if ($result -eq 0) {
                $metricsReady = $false
                $metricsUrl = 'http://127.0.0.1:9090/api/v1/query?query=count%28container_memory_working_set_bytes%7Bcontainer_label_com_docker_compose_project%3D%22b2c%22%7D%29'
                for ($attempt = 1; $attempt -le 12; $attempt++) {
                    $savedPreference = $ErrorActionPreference
                    $ErrorActionPreference = "Continue"
                    $metricsOutput = & wsl.exe -d $distro -- bash -lc "docker exec b2c-prometheus-1 wget -qO- '$metricsUrl'" 2>&1
                    $metricsResult = $LASTEXITCODE
                    $ErrorActionPreference = $savedPreference
                    $metricsText = $metricsOutput -join "`n"
                    foreach ($line in $metricsOutput) { Write-Log ([string]$line) }
                    if ($metricsResult -eq 0 -and $metricsText -match '"value":\[[0-9.]+,"[1-9][0-9]*"\]') {
                        $metricsReady = $true
                        Write-Log "Prometheus exposes B2C-only cAdvisor memory series."
                        break
                    }
                    Start-Sleep -Seconds 10
                }
                if ($metricsReady) {
                    Write-Log "B2C Compose start and post-start memory metrics check completed successfully."
                    exit 0
                }
                Write-Log "Prometheus did not expose B2C cAdvisor memory series after 2 minutes."
                exit 1
            }
            Write-Log "B2C Compose start failed with exit code $result; will retry."
        }
    }
    Start-Sleep -Seconds 10
}
Write-Log "Timed out after 15 minutes waiting for the mounted B2C disk and Docker."
exit 1

