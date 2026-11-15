# install.ps1 — download and install the `agentry` binary from GitHub Releases.
#
# Installs the one-folder build (`agentry.exe` plus its `_internal\` libraries) into the
# install dir, plus the short aliases `agy.cmd` and `agyx.cmd`. `agy` is also the command
# for Google's Antigravity CLI; `agyx` is the short name that cannot collide. Releases
# without a folder archive (before 0.3.5) install the single-file `agentry.exe` instead.
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

  # Prefer the one-folder zip (0.3.5 and later). Otherwise use the single-file exe.
  # Release assets were named agy-<version>-<target>.exe before 0.1.4: that name is tried
  # last, so this script can still install releases older than 0.1.4, which only published
  # agy-* assets. (Copies of this script older than 0.1.4 can't install current releases:
  # those no longer publish agy-* assets.)
  $asset = $null
  foreach ($candidate in @("agentry-$version_no_v-$target.zip", "agentry-$version_no_v-$target.exe", "agy-$version_no_v-$target.exe")) {
    if ($sums -match "  $([regex]::Escape($candidate))$") { $asset = $candidate; break }
  }
  if (-not $asset) { throw "no asset for $target in release $tag" }

  Write-Host "Downloading $asset ($tag)…"
  # Saved under the asset's own name: Expand-Archive refuses a file without a .zip suffix.
  $download = Join-Path $tmp $asset
  Invoke-WebRequest "$base/$asset" -OutFile $download

  $line = Select-String -Path "$tmp\SHA256SUMS.txt" -Pattern "  $([regex]::Escape($asset))$" | Select-Object -First 1
  if (-not $line) { throw "no checksum entry for $asset" }
  $expected = ($line.Line -split '\s+')[0].ToLower()
  $actual = (Get-FileHash $download -Algorithm SHA256).Hash.ToLower()
  if ($expected -ne $actual) { throw "checksum mismatch (expected $expected, got $actual)" }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  if ($asset.EndsWith('.zip')) {
    # The zip holds one agentry\ folder: agentry.exe and _internal\. Unpack it in the temp
    # dir first, then replace _internal\ whole, so no library from an older version stays.
    Expand-Archive -Path $download -DestinationPath "$tmp\unzipped"
    if (-not (Test-Path "$tmp\unzipped\agentry\agentry.exe")) { throw "$asset has no agentry\agentry.exe" }
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$InstallDir\_internal"
    Move-Item -Force "$tmp\unzipped\agentry\_internal" "$InstallDir\_internal"
    Move-Item -Force "$tmp\unzipped\agentry\agentry.exe" "$InstallDir\agentry.exe"
  } else {
    Move-Item -Force $download "$InstallDir\agentry.exe"
    # A single-file exe doesn't use _internal\; drop one left by a folder install.
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$InstallDir\_internal"
  }
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
