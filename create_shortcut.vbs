Set WshShell = WScript.CreateObject("WScript.Shell")
strDesktop = WshShell.SpecialFolders("Desktop")

' 先删除旧快捷方式
On Error Resume Next
Set fso = CreateObject("Scripting.FileSystemObject")
fso.DeleteFile strDesktop & "\Firefight AI.lnk", True
On Error GoTo 0

Set oShortcut = WshShell.CreateShortcut(strDesktop & "\Firefight AI.lnk")
oShortcut.TargetPath = "C:\Users\19853\AppData\Local\Programs\Python\Python314\python.exe"
oShortcut.Arguments = "d:\firefightAI\zhanluxt\desktop_app.py"
oShortcut.WorkingDirectory = "d:\firefightAI\zhanluxt"
oShortcut.WindowStyle = 1
oShortcut.IconLocation = "shell32.dll,13"
oShortcut.Description = "Firefight AI Tactical Command System v5.0"
oShortcut.Save
WScript.Echo "桌面快捷方式已创建: " & strDesktop & "\Firefight AI.lnk"