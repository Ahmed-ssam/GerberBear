# Builds GerberBear-Setup-<version>.exe with Inno Setup 6.
# Downloads (once, cached in .\downloads): embeddable Python 3.12, get-pip, Temurin JRE 21.
# Everything the installed app needs is bundled; KiCad stays a separate install.
param([string]$Version = "")
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Resolve-Path (Join-Path $here "..")            # the GerberBear folder
if (-not $Version) {                                   # from ai_pcb_designer\core\version.py
  $m = Select-String -Path "$root\app\ai_pcb_designer\core\version.py" -Pattern '__version__ = "([^"]+)"'
  $Version = $m.Matches[0].Groups[1].Value
}
Write-Host "Building Gerber Bear $Version"
Set-Location $here
$dl = Join-Path $here "downloads"; New-Item -ItemType Directory -Force $dl | Out-Null
$build = Join-Path $here "build"
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force "$build\python", "$build\app" | Out-Null

$py = "3.12.10"
$pyZip = "$dl\python-$py-embed-amd64.zip"
if (-not (Test-Path $pyZip)) {
  Write-Host "Downloading Python $py (embeddable)..."
  Invoke-WebRequest "https://www.python.org/ftp/python/$py/python-$py-embed-amd64.zip" -OutFile $pyZip
}
Expand-Archive $pyZip -DestinationPath "$build\python" -Force
# enable site-packages and let the app folder be importable
$pth = Get-ChildItem "$build\python" -Filter "python3*._pth" | Select-Object -First 1
Set-Content $pth.FullName "python312.zip`r`n.`r`nLib\site-packages`r`n..\app`r`nimport site`r`n" -Encoding ascii

$getpip = "$dl\get-pip.py"
if (-not (Test-Path $getpip)) { Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip }
Write-Host "Installing pip and packages into the bundled Python (2-5 minutes)..."
& "$build\python\python.exe" $getpip --no-warn-script-location | Out-Null
& "$build\python\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check `
    -r "$root\app\requirements-win.txt" --target "$build\python\Lib\site-packages"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$jreZip = "$dl\temurin-jre-21.zip"
if (-not (Test-Path $jreZip)) {
  Write-Host "Downloading Temurin JRE 21..."
  Invoke-WebRequest "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse?project=jdk" -OutFile $jreZip
}

Write-Host "Copying the app..."
robocopy "$root\app" "$build\app" /E /XD __pycache__ .pytest_cache logs /XF *.pyc /NFL /NDL /NJH /NJS | Out-Null
# GitHub repository (owner/name) for the About box, "Report a problem" and update checks
$remote = (git -C $root config --get remote.origin.url) 2>$null
if ($remote -match 'github\.com[:/]([^/]+/[^/.]+)') {
  Set-Content "$build\app\ai_pcb_designer\resources\repo.txt" $Matches[1] -Encoding ascii
  Write-Host "Repository: $($Matches[1])"
}
Remove-Item -Recurse -Force "$build\app\tests" -ErrorAction SilentlyContinue
$tmp = Join-Path $build "jre_tmp"
Expand-Archive $jreZip -DestinationPath $tmp -Force
$jreDir = Get-ChildItem $tmp -Directory | Select-Object -First 1
Move-Item $jreDir.FullName "$build\app\tools\jre"
Remove-Item -Recurse -Force $tmp

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 (ISCC.exe) not found" }
& $iscc "/DAppVersion=$Version" "GerberBear.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host ""
Write-Host "Done: $here\output\GerberBear-Setup.exe  (version $Version)"
