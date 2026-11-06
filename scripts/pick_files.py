import json
import os
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog


def configure_tk_libraries():
    # A Windows venv can find the Tcl/Tk DLLs but miss their script libraries.
    # Resolve these from the base Python install, not the venv or working folder.
    if sys.platform != 'win32':
        return
    library_root = Path(sys.base_prefix) / 'tcl'
    for variable, folder, entry in (
        ('TCL_LIBRARY', f'tcl{tk.TclVersion}', 'init.tcl'),
        ('TK_LIBRARY', f'tk{tk.TkVersion}', 'tk.tcl'),
    ):
        configured = os.environ.get(variable)
        if configured and (Path(configured) / entry).is_file():
            continue
        installed = library_root / folder
        if (installed / entry).is_file():
            os.environ[variable] = str(installed)


def select_files():
    configure_tk_libraries()
    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes('-topmost', True)
        return list(filedialog.askopenfilenames(
            title='导入游戏录像',
            filetypes=[('视频文件', '*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts'), ('所有文件', '*.*')],
            parent=root,
        ))
    finally:
        root.destroy()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8')
    print(json.dumps(select_files(), ensure_ascii=False))
