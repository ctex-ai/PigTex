!macro customInit
  ; Offer users a controlled upgrade path when an older PigTex install exists.
  StrCpy $R0 "$LOCALAPPDATA\Programs\PigTex\Uninstall PigTex.exe"
  IfFileExists "$R0" 0 check_program_files
  Goto ask_install_mode

check_program_files:
  StrCpy $R0 "$PROGRAMFILES64\PigTex\Uninstall PigTex.exe"
  IfFileExists "$R0" 0 done

ask_install_mode:
  MessageBox MB_ICONQUESTION|MB_YESNO "An existing PigTex installation was found.$\r$\n$\r$\nYes: Uninstall old version first, then install this version.$\r$\nNo: Keep old version and install this version side-by-side." IDYES do_uninstall IDNO do_parallel

 do_uninstall:
  ExecWait '"$R0" /S'
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\PigTex"
  Goto done

 do_parallel:
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\PigTex-${VERSION}"
  Goto done

 done:
!macroend
