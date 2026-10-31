# Adds a "DeckLens" shortcut to your desktop and Start menu. Run once:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-desktop.ps1
#
# The shortcut runs the DeckLens desktop app with this project's Python
# (pythonw: no console window). Run it again if you move the project folder.

$project = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $project ".venv\Scripts\pythonw.exe"
$icon = Join-Path $project "src\decklens\web\static\decklens.ico"

if (-not (Test-Path $pythonw)) {
    Write-Host "Couldn't find $pythonw - create the virtual environment first (see README)."
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Microsoft\Windows\Start Menu\Programs")
)
foreach ($folder in $places) {
    $shortcut = $shell.CreateShortcut((Join-Path $folder "DeckLens.lnk"))
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "-m decklens.desktop"
    $shortcut.WorkingDirectory = $project
    $shortcut.IconLocation = $icon
    $shortcut.Description = "DeckLens: Clash Royale matchup scouting"
    $shortcut.Save()
    Write-Host "Added DeckLens to $folder"
}
Write-Host "Done. Open DeckLens from the desktop or Start menu."
