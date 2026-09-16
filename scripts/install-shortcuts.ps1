<#
.SYNOPSIS
    Create (or remove) WinMonitor shortcuts on the desktop and Start Menu.

.DESCRIPTION
    Adds "WinMonitor" to the desktop and the Start Menu, plus a
    "WinMonitor (Administrator)" Start Menu entry that prompts for elevation.

    Everything it touches belongs to the current user: no machine-wide changes,
    no administrator rights needed to run it.

.PARAMETER ExePath
    Point the shortcuts at a specific winmonitor.exe - use this with the
    standalone build, which is not installed anywhere for the script to find.

.PARAMETER Uninstall
    Remove the shortcuts instead of creating them.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1 -ExePath C:\Tools\winmonitor.exe

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$ExePath,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$desktop  = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')

$targets = @(
    @{ Path = Join-Path $desktop  'WinMonitor.lnk';                 Admin = $false }
    @{ Path = Join-Path $programs 'WinMonitor.lnk';                 Admin = $false }
    @{ Path = Join-Path $programs 'WinMonitor (Administrator).lnk'; Admin = $true  }
)

if ($Uninstall) {
    $removed = 0
    foreach ($t in $targets) {
        if (Test-Path $t.Path) {
            Remove-Item $t.Path -Force
            Write-Host "Removed $($t.Path)"
            $removed++
        }
    }
    if ($removed -eq 0) { Write-Host 'No WinMonitor shortcuts found.' }
    return
}

# An explicit path wins: that is how the standalone build is pointed at, since
# it is not installed anywhere this script could discover it.
$exe = $null
if ($ExePath) {
    $resolved = Resolve-Path -LiteralPath $ExePath -ErrorAction SilentlyContinue
    if (-not $resolved) { Write-Error "No such file: $ExePath" }
    $exe = $resolved.Path
}

# Otherwise find the pip-installed one. It sits beside pip's other console
# scripts, which may not be on PATH, so ask Python where that folder is.
if (-not $exe) { $exe = (Get-Command winmonitor -ErrorAction SilentlyContinue).Source }
if (-not $exe) {
    $scriptsDir = & python -c "import sysconfig;print(sysconfig.get_path('scripts','nt_user'))" 2>$null
    if ($scriptsDir) { $candidate = Join-Path $scriptsDir 'winmonitor.exe' }
    if ($candidate -and (Test-Path $candidate)) { $exe = $candidate }
}
if (-not $exe) {
    $scriptsDir = & python -c "import sysconfig;print(sysconfig.get_path('scripts'))" 2>$null
    if ($scriptsDir) { $candidate = Join-Path $scriptsDir 'winmonitor.exe' }
    if ($candidate -and (Test-Path $candidate)) { $exe = $candidate }
}
if (-not $exe) {
    Write-Error @'
Could not find winmonitor.exe.

Either install WinMonitor from the project folder:

    pip install -e .

or, if you have the standalone build, point this script at it:

    ... install-shortcuts.ps1 -ExePath C:\Tools\winmonitor.exe
'@
}

Write-Host "Using $exe"
$shell = New-Object -ComObject WScript.Shell

foreach ($t in $targets) {
    $link = $shell.CreateShortcut($t.Path)
    $link.TargetPath       = $exe
    $link.WorkingDirectory = $env:USERPROFILE
    $link.IconLocation     = "$exe,0"
    $link.Description      = if ($t.Admin) {
        'WinMonitor with administrator rights (prompts for elevation)'
    } else {
        'Terminal system, process and port monitor for Windows 11'
    }
    $link.Save()

    if ($t.Admin) {
        # WScript.Shell cannot set "run as administrator", so set the flag in
        # the .lnk itself: bit 0x20 of the byte at offset 0x15.
        $bytes = [IO.File]::ReadAllBytes($t.Path)
        $bytes[0x15] = $bytes[0x15] -bor 0x20
        [IO.File]::WriteAllBytes($t.Path, $bytes)
    }

    Write-Host "Created $($t.Path)"
}

Write-Host ''
Write-Host 'Done. Press Win and type "winmonitor", or use the desktop icon.'
