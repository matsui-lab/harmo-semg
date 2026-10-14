param([ValidateSet('setup','start','check')][string]$Mode='start')
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This preview supports Windows x64. Other architectures require separate validation.' }
$env:UV_PYTHON_INSTALL_DIR=Join-Path $PSScriptRoot '.runtime/python'
$env:UV_CACHE_DIR=Join-Path $PSScriptRoot '.runtime/cache'
$env:UV_NO_CONFIG='1'
$Uv=Join-Path $PSScriptRoot '.runtime/tools/uv.exe'
$Python=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if ($Mode -eq 'setup') {
  if (!(Test-Path $Uv)) {
    $Stage=Join-Path $PSScriptRoot ('.runtime/download-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null
    $Archive=Join-Path $Stage 'uv.zip'
    Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.12.15/uv-x86_64-pc-windows-msvc.zip' -OutFile $Archive
    if ((Get-FileHash $Archive -Algorithm SHA256).Hash.ToLower() -ne '477bd99a84e34891f2bd4c9152ddeb74e971accccbc59c0f0301f11f08a32d46') { throw 'Download checksum mismatch; stopping.' }
    Expand-Archive -LiteralPath $Archive -DestinationPath $Stage
    New-Item -ItemType Directory -Path (Split-Path $Uv) -Force | Out-Null
    Copy-Item (Join-Path $Stage 'uv.exe') $Uv
  }
  if (!(Test-Path $Python)) {
    & $Uv venv --python 3.12 --managed-python .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
  }
  & $Python manage.py setup --uv $Uv
} else {
  if (!(Test-Path $Python)) { throw 'Run Setup first.' }
  & $Python manage.py $Mode
}
if ($LASTEXITCODE -ne 0) { throw 'Harmo stopped with an error. Check the messages above.' }
