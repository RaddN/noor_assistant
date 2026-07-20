Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
pythonw = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
python = fso.BuildPath(root, ".venv\Scripts\python.exe")
If fso.FileExists(pythonw) Then
  shell.Run """" & pythonw & """ main.py", 0, False
Else
  shell.Run """" & python & """ main.py", 0, False
End If
