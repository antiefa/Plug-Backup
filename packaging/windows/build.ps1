$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root
$output = Join-Path $PSScriptRoot 'dist'
$locale = Join-Path $PSScriptRoot 'locale'
$venv = Join-Path $PSScriptRoot '.venv-build'

if (Test-Path $output) { Remove-Item -Recurse -Force $output }
New-Item -ItemType Directory -Force -Path $output, $locale | Out-Null
Get-ChildItem $locale -Filter '*.mo' -File | Remove-Item -Force

$versionLine = Select-String -Path (Join-Path $root 'plugbackup\__init__.py') -Pattern '^__version__\s*=\s*["'']([^"'']+)["'']'
if (-not $versionLine) { throw 'Не удалось прочитать версию из plugbackup/__init__.py' }
$version = $versionLine.Matches[0].Groups[1].Value
$numericVersion = (($version -split '[^0-9]+') | Where-Object { $_ -ne '' } | Select-Object -First 4) -join '.'
while (($numericVersion.Split('.').Count) -lt 4) { $numericVersion += '.0' }

if (-not (Test-Path $venv)) { python -m venv $venv }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements-build.txt')

foreach ($po in Get-ChildItem (Join-Path $root 'po') -Filter '*.po' -File) {
    $mo = Join-Path $locale ($po.BaseName + '.mo')
    & $python (Join-Path $PSScriptRoot 'build_mo.py') $po.FullName $mo
    if ($LASTEXITCODE -ne 0) { throw "Не удалось собрать перевод $($po.Name)" }
}

& $python -m PyInstaller --clean --noconfirm --distpath $output --workpath (Join-Path $PSScriptRoot 'build\cli') (Join-Path $PSScriptRoot 'plugbackup-cli.spec')
if ($LASTEXITCODE -ne 0) { throw 'Сборка plugbackup-cli.exe завершилась с ошибкой' }
& $python -m PyInstaller --clean --noconfirm --distpath $output --workpath (Join-Path $PSScriptRoot 'build\gui') (Join-Path $PSScriptRoot 'PlugBackup.spec')
if ($LASTEXITCODE -ne 0) { throw 'Сборка PlugBackup.exe завершилась с ошибкой' }

$iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { $iscc = Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { throw 'Не найден Inno Setup 6 (ISCC.exe)' }
& $iscc "/DAppVersion=$version" "/DAppNumericVersion=$numericVersion" "/DOutputDir=`"$output`"" (Join-Path $PSScriptRoot 'PlugBackup.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup завершился с ошибкой' }

$installer = Get-ChildItem $output -Filter 'PlugBackup-*-setup.exe' -File | Select-Object -First 1
if (-not $installer) { throw 'Установщик не создан' }
$hash = (Get-FileHash -Algorithm SHA256 $installer.FullName).Hash.ToLowerInvariant()
Set-Content -Encoding ascii -Path (Join-Path $output 'SHA256SUMS') -Value "$hash  $($installer.Name)"
Write-Host "Готово: $($installer.FullName)"
