# Builds a Blender extension zip for one add-on in blender/<name> into dist/.
# Usage: .\scripts\build_addon.ps1 sb_ai_retopo [-Blender "C:\path\to\blender.exe"]
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Blender = ""
)

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "blender\$Name"
$out = Join-Path $root "dist"

if (-not (Test-Path $src)) { throw "Add-on folder not found: $src" }
# Blender's build command does not create the output directory itself
New-Item -ItemType Directory -Force $out | Out-Null

if ($Blender -eq "") {
    $candidates = Get-ChildItem "C:\Program Files\Blender Foundation" -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "blender.exe" } |
        Where-Object { Test-Path $_ }
    if (-not $candidates) { throw "blender.exe not found; pass -Blender" }
    $Blender = $candidates | Select-Object -First 1
}

& $Blender --command extension build --source-dir $src --output-dir $out
