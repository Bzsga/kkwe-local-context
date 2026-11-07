param(
    [string]$EditorRoot = '',
    [string]$Python = '',
    [switch]$Launch
)
$ErrorActionPreference = 'Stop'
$launcherState = Join-Path $PSScriptRoot 'state\launcher.json'
$settings = $null
if (Test-Path -LiteralPath $launcherState) {
    try {
        $settings = Get-Content -LiteralPath $launcherState -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($settings -isnot [pscustomobject]) { $settings = $null }
    }
    catch { $settings = $null }
}
if ([string]::IsNullOrWhiteSpace($EditorRoot)) { $EditorRoot = $env:KKLC_EDITOR_ROOT }
if ([string]::IsNullOrWhiteSpace($EditorRoot) -and $settings -and $settings.editor_root -is [string]) {
    $EditorRoot = $settings.editor_root
}
if ([string]::IsNullOrWhiteSpace($EditorRoot)) {
    $toolParent = Split-Path -Parent $PSScriptRoot
    if ((Split-Path -Leaf $toolParent) -ieq 'tools') {
        $legacyEditor = Join-Path (Split-Path -Parent $toolParent) 'KKWE插件'
        if (Test-Path -LiteralPath (Join-Path $legacyEditor 'share\script\compile\wave.lua') -PathType Leaf) {
            $EditorRoot = $legacyEditor
        }
    }
}
if ([string]::IsNullOrWhiteSpace($EditorRoot)) {
    throw 'KKWE editor directory is not configured. Use -EditorRoot, KKLC_EDITOR_ROOT, or select a directory in gui.py.'
}
$EditorRoot = $EditorRoot.Trim()
if (-not $Python) {
    if ($settings -and $settings.python) { $Python = $settings.python }
    else { $Python = (Get-Command python.exe -ErrorAction Stop).Source }
}
$EditorRoot = (Resolve-Path -LiteralPath $EditorRoot).Path
& $Python -X utf8 -B (Join-Path $PSScriptRoot 'manager.py') preflight --editor-root $EditorRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Checking/repairing does not authorize starting the editor. The caller must
# explicitly request -Launch when editor startup is authorized.
if ($Launch) {
    $editorExecutable = Join-Path $EditorRoot 'KKWE.exe'
    if (-not (Test-Path -LiteralPath $editorExecutable)) { throw "KKWE.exe missing: $editorExecutable" }
    Start-Process -FilePath $editorExecutable -WorkingDirectory $EditorRoot -WindowStyle Hidden
}
