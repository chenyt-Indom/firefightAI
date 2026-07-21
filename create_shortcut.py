"""Create desktop shortcut for Firefight AI"""
import os
import sys
import pythoncom
from win32com.client import Dispatch

def create_shortcut():
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    shortcut_path = os.path.join(desktop, "Firefight AI.lnk")
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.bat")
    working_dir = os.path.dirname(os.path.abspath(__file__))

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(shortcut_path)
    shortcut.TargetPath = "cmd.exe"
    shortcut.Arguments = f'/c ""{target}""'
    shortcut.WorkingDirectory = working_dir
    shortcut.Description = "Firefight AI Tactical Command System v3.0"
    shortcut.IconLocation = "shell32.dll,13"
    shortcut.WindowStyle = 1
    shortcut.Save()

    print(f"Shortcut created: {shortcut_path}")
    return shortcut_path

if __name__ == "__main__":
    pythoncom.CoInitialize()
    try:
        create_shortcut()
        print("Done!")
    finally:
        pythoncom.CoUninitialize()