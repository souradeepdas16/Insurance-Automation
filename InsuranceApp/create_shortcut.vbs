Set WshShell = CreateObject("WScript.Shell")
Set oShortcut = WshShell.CreateShortcut(WshShell.SpecialFolders("Desktop") & "\Insurance App.lnk")

oShortcut.TargetPath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
oShortcut.Arguments = "--app=""http://168.144.27.133:8000/"""
oShortcut.WorkingDirectory = "C:\Program Files\Google\Chrome\Application"
oShortcut.Description = "Insurance Automation App"
oShortcut.IconLocation = "C:\Program Files\Google\Chrome\Application\chrome.exe, 0"
oShortcut.Save

WScript.Echo "Desktop shortcut created: Insurance App"
