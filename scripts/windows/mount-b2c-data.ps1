#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [switch]$InstallAutoMount,
    # Must match the WSL distro that runs Docker (`wsl -l -v`). A stale name here made
    # every check fail silently after the distro was moved to "B2C-On-D".
    [string]$Distro = "B2C-On-D"
)

$ErrorActionPreference = "Stop"
$vhdPath = "F:\wsl-b2c\b2c-data.vhdx"
$distro = $Distro
$mountPath = "/mnt/wsl/b2cdata"
$marker = ".b2c-data-disk"
$taskName = "B2C-Data-Disk-Mount"
$installPath = Join-Path $env:ProgramData "B2C\mount-b2c-data.ps1"
$logPath = Join-Path $env:ProgramData "B2C\mount-b2c-data.log"
New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null

function Write-Log([string]$Message) {
    $line = "{0:u} {1}" -f (Get-Date), $Message
    Add-Content -LiteralPath $logPath -Value $line
    Write-Output $line
}

try {
    $known = (& wsl.exe -l -q) -replace "`0", "" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    if ($known -notcontains $distro) {
        throw "WSL distro '$distro' does not exist (found: $($known -join ', ')). Pass -Distro."
    }
    if (-not (Test-Path -LiteralPath $vhdPath -PathType Leaf)) {
        throw "B2C data VHDX not found at the configured path: $vhdPath"
    }

    $existing = & wsl.exe -d $distro -- bash -lc "findmnt -rn -M '$mountPath' -o TARGET,SOURCE,FSTYPE"
    if ($LASTEXITCODE -eq 0 -and $existing) {
        & wsl.exe -d $distro -- bash -lc "test -f '$mountPath/$marker' && test -d '$mountPath/models'"
        if ($LASTEXITCODE -ne 0) { throw "Mountpoint exists but the B2C marker/models check failed; refusing to use it." }
        Write-Log "Already mounted and marker verified: $existing"
    } else {
        Write-Log "Attaching the existing VHDX (no format/initialize): $vhdPath"
        $mountOutput = & wsl.exe --mount --vhd $vhdPath --name b2cdata 2>&1
        foreach ($l in $mountOutput) { Write-Log ("wsl --mount: " + ([string]$l -replace "`0", "")) }
        if ($LASTEXITCODE -ne 0) {
            throw "WSL could not attach the B2C VHDX. Keep the file intact and inspect the WSL error."
        }
        $deadline = (Get-Date).AddSeconds(40)
        do {
            Start-Sleep -Seconds 2
            $existing = & wsl.exe -d $distro -- bash -lc "findmnt -rn -M '$mountPath' -o TARGET,SOURCE,FSTYPE"
        } until (($LASTEXITCODE -eq 0 -and $existing) -or (Get-Date) -ge $deadline)
        if ($LASTEXITCODE -ne 0 -or -not $existing) {
            throw "WSL attached the disk but $mountPath is not an exact mountpoint."
        }
        & wsl.exe -d $distro -- bash -lc "test -f '$mountPath/$marker' && test -d '$mountPath/models'"
        if ($LASTEXITCODE -ne 0) { throw "The mounted filesystem does not contain the expected B2C marker and models directory." }
        Write-Log "Mounted and marker verified: $existing"
    }

    if ($InstallAutoMount) {
        if ($PSCommandPath -ne $installPath) {
            Copy-Item -LiteralPath $PSCommandPath -Destination $installPath -Force
        }
        $actionArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $installPath + '"'
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
        $description = "Mount existing B2C data VHDX at sign-in; verify marker; never format."
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Description $description -Force | Out-Null
        Write-Log "Registered verified at-logon task: $taskName -> $installPath"
        $task = Get-ScheduledTask -TaskName $taskName
        if (-not $task) { throw "Scheduled task registration could not be verified." }
        Write-Log "Task verification: state=$($task.State); runLevel=$($task.Principal.RunLevel); user=$($task.Principal.UserId)"
    }
} catch {
    Write-Log ("FAILED: " + $_.Exception.Message)
    exit 1
}
