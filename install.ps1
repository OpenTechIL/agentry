# install.ps1 — download and install the `agy` binary from GitHub Releases.
#
#   irm https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.ps1 | iex
#
# Env: AGENTRY_VERSION (default: latest), AGENTRY_INSTALL_DIR
#Requires -Version 5
$ErrorActionPreference = 'Stop'

$Repo = 'OpenTechIL/agentry'
$InstallDir = if ($env:AGENTRY_INSTALL_DIR) { $env:AGENTRY_INSTALL_DIR } else { "$env:LOCALAPPDATA\Programs\agentry" }

$arch = switch ($env:PROCESSOR_ARCHITECTURE) {
  'AMD64' { 'x86_64' }
  'ARM64' { 'arm64' }
  default { throw "unsupported arch: $env:PROCESSOR_ARCHITECTURE" }
}
if ($arch -eq 'arm64') { throw "no prebuilt binary for windows-arm64 yet; use 'uv tool install agentry'" }
$target = "windows-$arch"

$version = if ($env:AGENTRY_VERSION) { $env:AGENTRY_VERSION } else { 'latest' }
if ($version -eq 'latest') {
  $tag = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
} else {
  $tag = "v$($version.TrimStart('v'))"
}
$asset = "agy-$($tag.TrimStart('v'))-$target.exe"
$base  = "https://github.com/$Repo/releases/download/$tag"

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  Write-Host "Downloading $asset ($tag)…"
  Invoke-WebRequest "$base/$asset" -OutFile "$tmp\agy.exe"
  Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile "$tmp\SHA256SUMS.txt"

  $line = Select-String -Path "$tmp\SHA256SUMS.txt" -Pattern ([regex]::Escape($asset)) | Select-Object -First 1
  if (-not $line) { throw "no checksum entry for $asset" }
  $expected = $line.Line.Split(' ')[0].ToLower()
  $actual = (Get-FileHash "$tmp\agy.exe" -Algorithm SHA256).Hash.ToLower()
  if ($expected -ne $actual) { throw "checksum mismatch (expected $expected, got $actual)" }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  Move-Item -Force "$tmp\agy.exe" "$InstallDir\agy.exe"
  Write-Host "Installed agy to $InstallDir\agy.exe"

  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if ($userPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$InstallDir", 'User')
    Write-Host "Added $InstallDir to your user PATH — restart your shell to pick it up."
  }
  & "$InstallDir\agy.exe" version
} finally {
  Remove-Item -Recurse -Force $tmp
}
