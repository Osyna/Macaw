; Macaw win64 installer — per-user (no admin), Slint UI + frozen engine.
; Build:  makensis /DVERSION=x.y.z /DSRCDIR=..\..\out\Macaw macaw.nsi
;   SRCDIR must contain macaw-ui.exe and macaw-engine.exe.

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef SRCDIR
  !define SRCDIR "."
!endif

Unicode true
Name "Macaw"
OutFile "Macaw_${VERSION}_x64-setup.exe"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Macaw"

!include "MUI2.nsh"
!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\macaw-ui.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start Macaw"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!define ARP "Software\Microsoft\Windows\CurrentVersion\Uninstall\Macaw"

Section "Install"
  ; Best effort: stop a running instance so the exe isn't locked.
  nsExec::Exec 'taskkill /IM macaw-ui.exe /F'
  nsExec::Exec 'taskkill /IM macaw-engine.exe /F'

  SetOutPath "$INSTDIR"
  File "${SRCDIR}\macaw-ui.exe"
  File "${SRCDIR}\macaw-engine.exe"
  File "icon.ico"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  CreateShortcut "$SMPROGRAMS\Macaw.lnk" "$INSTDIR\macaw-ui.exe" "" "$INSTDIR\icon.ico"

  WriteRegStr HKCU "${ARP}" "DisplayName" "Macaw"
  WriteRegStr HKCU "${ARP}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${ARP}" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "${ARP}" "Publisher" "Osyna"
  WriteRegStr HKCU "${ARP}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${ARP}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD HKCU "${ARP}" "NoModify" 1
  WriteRegDWORD HKCU "${ARP}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /IM macaw-ui.exe /F'
  nsExec::Exec 'taskkill /IM macaw-engine.exe /F'

  Delete "$INSTDIR\macaw-ui.exe"
  Delete "$INSTDIR\macaw-engine.exe"
  Delete "$INSTDIR\icon.ico"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\Macaw.lnk"
  DeleteRegKey HKCU "${ARP}"
  ; Config (%APPDATA%\macaw) and backends (%LOCALAPPDATA%\macaw) are kept —
  ; delete those folders yourself if you want a clean slate.
SectionEnd
