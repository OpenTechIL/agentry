# install.ps1 — download and install the `agentry` binary from GitHub Releases.
#
# Installs `agentry.exe` plus the short aliases `agy.cmd` and `agyx.cmd`. `agy` is also the
# command for Google's Antigravity CLI; `agyx` is the short name that cannot collide.
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
if ($arch -eq 'arm64') { throw "no prebuilt binary for windows-arm64 yet; use 'uv tool install git+https://github.com/OpenTechIL/agentry'" }
$target = "windows-$arch"

$version = if ($env:AGENTRY_VERSION) { $env:AGENTRY_VERSION } else { 'latest' }
if ($version -eq 'latest') {
  $tag = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
} else {
  $tag = "v$($version.TrimStart('v'))"
}
$version_no_v = $tag.TrimStart('v')
$base  = "https://github.com/$Repo/releases/download/$tag"

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile "$tmp\SHA256SUMS.txt"
  $sums = Get-Content "$tmp\SHA256SUMS.txt"

  # Release assets were named agy-<version>-<target>.exe before 0.1.4. Prefer the current
  # name and fall back, so old and new copies of this script both keep working.
  $asset = $null
  foreach ($candidate in @("agentry-$version_no_v-$target.exe", "agy-$version_no_v-$target.exe")) {
    if ($sums -match "  $([regex]::Escape($candidate))$") { $asset = $candidate; break }
  }
  if (-not $asset) { throw "no asset for $target in release $tag" }

  Write-Host "Downloading $asset ($tag)…"
  Invoke-WebRequest "$base/$asset" -OutFile "$tmp\agentry.exe"

  $line = Select-String -Path "$tmp\SHA256SUMS.txt" -Pattern "  $([regex]::Escape($asset))$" | Select-Object -First 1
  if (-not $line) { throw "no checksum entry for $asset" }
  $expected = ($line.Line -split '\s+')[0].ToLower()
  $actual = (Get-FileHash "$tmp\agentry.exe" -Algorithm SHA256).Hash.ToLower()
  if ($expected -ne $actual) { throw "checksum mismatch (expected $expected, got $actual)" }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  Move-Item -Force "$tmp\agentry.exe" "$InstallDir\agentry.exe"
  # Alias shims. Windows offers no reliable symlink for an unprivileged install, so a
  # one-line .cmd wrapper is the portable equivalent. An older install left a real
  # agy.exe here; remove it so the alias wins rather than a stale binary.
  Remove-Item -Force -ErrorAction SilentlyContinue "$InstallDir\agy.exe"
  foreach ($aliasName in @('agy', 'agyx')) {
    Set-Content -Path "$InstallDir\$aliasName.cmd" -Encoding ASCII `
      -Value "@echo off`r`n`"%~dp0agentry.exe`" %*"
  }
  Write-Host "Installed agentry to $InstallDir\agentry.exe (aliases: agy, agyx)"

  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if (($userPath -split ';') -notcontains $InstallDir) {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$InstallDir", 'User')
    Write-Host "Added $InstallDir to your user PATH — restart your shell to pick it up."
  }
  & "$InstallDir\agentry.exe" version
} finally {
  Remove-Item -Recurse -Force $tmp
}
