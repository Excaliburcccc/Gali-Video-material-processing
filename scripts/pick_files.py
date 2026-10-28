import json
import sys
import tkinter as tk
from tkinter import filedialog

sys.stdout.reconfigure(encoding='utf-8')
root=tk.Tk()
root.withdraw()
root.attributes('-topmost',True)
paths=filedialog.askopenfilenames(title='导入游戏录像',filetypes=[('视频文件','*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts'),('所有文件','*.*')],parent=root)
root.destroy()
print(json.dumps(list(paths),ensure_ascii=False))
