' Launch Pipeline GUI.vbs
' Double-click this file to open the Texture Library Pipeline GUI.
' Uses pythonw.exe so no console window appears.

Dim fso, shell, scriptDir, guiPath
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
guiPath   = scriptDir & "\pipeline_gui.py"

If Not fso.FileExists(guiPath) Then
    MsgBox "Could not find pipeline_gui.py next to this launcher." & vbCrLf & _
           "Expected: " & guiPath, vbCritical, "Texture Library Pipeline"
    WScript.Quit 1
End If

shell.Run """C:\Python314\pythonw.exe"" """ & guiPath & """", 0, False
