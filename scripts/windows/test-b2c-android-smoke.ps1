param([Parameter(Mandatory=$true)][string]$Apk,[string]$Sdk='C:\Android\sdk',[string]$Evidence=(Join-Path $env:TEMP 'b2c-android-smoke'),[switch]$Stress)
$ErrorActionPreference='Stop'
$env:ANDROID_ADB_SERVER_PORT='5038'
$testMutex=[Threading.Mutex]::new($false,'Local\B2CAndroidSmoke')
if(!$testMutex.WaitOne(0)){throw 'Another B2C Android smoke test is running.'}
try {
$adb=Join-Path $Sdk 'platform-tools\adb.exe'
New-Item -ItemType Directory -Force -Path $Evidence | Out-Null
$install=& $adb -e install -r $Apk
if ($install -notcontains 'Success') { throw ($install -join "`n") }
& $adb -e logcat -c
& $adb -e shell am start -W -n com.book2course.operator/.MainActivity | Set-Content (Join-Path $Evidence 'launch.txt')
Start-Sleep -Seconds 3
function Save-Screen([string]$Name) {
 & $adb -e shell uiautomator dump /sdcard/b2c-window.xml | Out-Null
 & $adb -e pull /sdcard/b2c-window.xml (Join-Path $Evidence ($Name+'.xml')) | Out-Null
 & $adb -e shell screencap -p /sdcard/b2c-screen.png
 & $adb -e pull /sdcard/b2c-screen.png (Join-Path $Evidence ($Name+'.png')) | Out-Null
 [xml]$xml=Get-Content -LiteralPath (Join-Path $Evidence ($Name+'.xml')) -Raw -Encoding UTF8
 return $xml
}
$ui=Save-Screen 'library'
foreach($tab in @(@('المراحل','stages','تقدم المعالجة'),@('النتائج','results','النتائج والقراءة'),@('الصوت','audio','تحديث قائمة الصوت'),@('الإعدادات','settings','عنوان خادم Book2Course'),@('المكتبة','library-return','مكتبتي'))) {
 $node=$ui.SelectNodes('//node') | Where-Object { $_.text -eq $tab[0] } | Select-Object -First 1
 if (!$node) { throw "Missing navigation tab: $($tab[0])" }
 $nums=[regex]::Matches($node.bounds,'\d+') | ForEach-Object { [int]$_.Value }
 $x=[int](($nums[0]+$nums[2])/2);$y=[int](($nums[1]+$nums[3])/2)
 & $adb -e shell input tap $x $y
 Start-Sleep -Seconds 1
 $ui=Save-Screen $tab[1]
 if(!($ui.SelectNodes('//node') | Where-Object { $_.text -eq $tab[2] })) { throw "Expected screen missing: $($tab[2])" }
}
& $adb -e shell input keyevent KEYCODE_HOME
Start-Sleep -Seconds 1
& $adb -e shell am start -W -n com.book2course.operator/.MainActivity | Set-Content (Join-Path $Evidence 'resume.txt')
$ui=Save-Screen 'resume'
& $adb -e shell dumpsys meminfo com.book2course.operator | Set-Content (Join-Path $Evidence 'memory.txt')
if($Stress){ & $adb -e shell monkey -p com.book2course.operator -s 20261008 --pct-syskeys 0 --pct-majornav 0 --pct-nav 0 --pct-appswitch 0 --throttle 200 100 | Set-Content (Join-Path $Evidence 'monkey.txt') }
& $adb -e logcat -d -s AndroidRuntime:E | Set-Content (Join-Path $Evidence 'crashes.txt')
$crashes=Get-Content (Join-Path $Evidence 'crashes.txt') -Raw
if($crashes -match 'FATAL EXCEPTION|UnsatisfiedLinkError') { throw 'Runtime crash found; inspect evidence.' }
$api=& $adb -e shell getprop ro.build.version.sdk
$abi=& $adb -e shell getprop ro.product.cpu.abi
"PASS: install, launch, five tabs, background/resume; API $api; ABI $abi; evidence $Evidence"
} finally { $testMutex.ReleaseMutex(); $testMutex.Dispose() }
