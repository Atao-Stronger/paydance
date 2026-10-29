# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import re
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo)

project = Path(SPECPATH)
version = re.search(r'^APP_VERSION = "([^"]+)"',
                    (project / 'paydance.pyw').read_text(encoding='utf-8'), re.M).group(1)
numbers = tuple(int(part) for part in version.split('.')) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3f, flags=0,
                     OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('080404b0', [
        StringStruct('CompanyName', '下班倒计时'),
        StringStruct('FileDescription', '下班倒计时'),
        StringStruct('FileVersion', version),
        StringStruct('InternalName', '下班倒计时'),
        StringStruct('OriginalFilename', '下班倒计时.exe'),
        StringStruct('ProductName', '下班倒计时'),
        StringStruct('ProductVersion', version)
    ])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])])

a = Analysis([str(project / 'paydance.pyw')], pathex=[str(project)],
             binaries=[], datas=[(str(project / 'paydance.ico'), '.')], hiddenimports=[], hookspath=[],
             hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
# Qt imports Windows' unversioned ICU API; a Git/MSYS ICU with versioned exports is incompatible.
a.binaries = [item for item in a.binaries
              if Path(item[0]).name.lower() not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='下班倒计时',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, version=version_info, icon=str(project / 'paydance.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               upx_exclude=[], name='下班倒计时')
