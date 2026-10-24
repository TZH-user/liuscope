' RK3562 serial terminal V6 launcher - no console window at all (recommended)
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
script_dir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & script_dir & "\serial_terminal_v6.ps1"""
sh.Run cmd, 0, False
