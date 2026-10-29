Option Explicit
Dim shell, files, folder, pythonPath, appPath, extra, arg
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
pythonPath = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"
If Not files.FileExists(pythonPath) Then pythonPath = "pythonw.exe"
appPath = folder & "\app.py"
extra = ""
For Each arg In WScript.Arguments
    If arg = "--demo" Then extra = " --demo"
Next
shell.CurrentDirectory = folder
shell.Environment("PROCESS")("PYTHONUTF8") = "1"
shell.Run Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & appPath & Chr(34) & extra, 0, False
