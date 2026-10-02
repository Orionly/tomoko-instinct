$WshShell = New-Object -comObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$Shortcut = $WshShell.CreateShortcut("$Desktop\Tomoko Brain v1.lnk")
$Shortcut.TargetPath = "wscript.exe"
$Shortcut.Arguments = """$PSScriptRoot\launch_hidden.vbs"""
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.IconLocation = "shell32.dll,13"
$Shortcut.Description = "Tomoko Brain v1 - 566 USC Micro Brain"
$Shortcut.Save()
Write-Host "Shortcut created on Desktop: Tomoko Brain v1.lnk"
