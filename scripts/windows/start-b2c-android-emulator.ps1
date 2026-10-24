param([string]$Avd='B2C_QA_API27',[string]$Sdk='C:\Android\sdk')
$ErrorActionPreference='Stop'
$env:ANDROID_ADB_SERVER_PORT='5038'
$env:ANDROID_SDK_ROOT=$Sdk
$emu=Join-Path $Sdk 'emulator\emulator.exe'
if (!(Test-Path -LiteralPath $emu)) { throw 'Android Emulator is not installed.' }
$adb=Join-Path $Sdk 'platform-tools\adb.exe'
$existing=([string]((& $adb -e shell getprop sys.boot_completed 2>$null) -join '')).Trim()
if($existing -eq '1'){Write-Output 'B2C Android emulator is already booted.';return}
Start-Process -FilePath $emu -ArgumentList @('-avd',$Avd,'-no-window','-no-snapshot','-gpu','off','-memory','2048','-cores','2','-no-boot-anim','-no-metrics','-feature','-Vulkan','-verbose') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $env:TEMP 'b2c-emulator-out.log') -RedirectStandardError (Join-Path $env:TEMP 'b2c-emulator-error.log')
$adb=Join-Path $Sdk 'platform-tools\adb.exe'
$deadline=(Get-Date).AddMinutes(5)
do { Start-Sleep -Seconds 5; $ready=[string](& $adb -e shell getprop sys.boot_completed 2>$null) } while (([string]$ready).Trim() -ne '1' -and (Get-Date) -lt $deadline)
if (([string]$ready).Trim() -ne '1') { throw 'Emulator boot timeout. See b2c-emulator-error.log in TEMP.' }
& $adb -e shell input keyevent 82
& $adb -e shell getprop ro.build.version.release
& $adb -e shell getprop ro.product.cpu.abi