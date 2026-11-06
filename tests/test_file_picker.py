"""Picker bootstrap and cleanup without opening a real dialog in the test suite."""
import importlib.util
import os
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    'local_file_picker', Path(__file__).resolve().parents[1] / 'scripts' / 'pick_files.py')
picker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(picker)


def test_windows_venv_finds_installed_tcl_and_tk(tmp_path, monkeypatch):
    monkeypatch.setattr(picker.sys, 'platform', 'win32')
    monkeypatch.setattr(picker.sys, 'base_prefix', str(tmp_path / 'Python'))
    for var, folder, entry in (
        ('TCL_LIBRARY', f'tcl{picker.tk.TclVersion}', 'init.tcl'),
        ('TK_LIBRARY', f'tk{picker.tk.TkVersion}', 'tk.tcl'),
    ):
        installed = tmp_path / 'Python' / 'tcl' / folder
        installed.mkdir(parents=True)
        (installed / entry).touch()
        monkeypatch.setenv(var, str(tmp_path / 'missing'))
    picker.configure_tk_libraries()
    assert (Path(os.environ['TCL_LIBRARY']) / 'init.tcl').is_file()
    assert (Path(os.environ['TK_LIBRARY']) / 'tk.tcl').is_file()


def test_explicit_valid_tcl_paths_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(picker.sys, 'platform', 'win32')
    for var, entry in [('TCL_LIBRARY', 'init.tcl'), ('TK_LIBRARY', 'tk.tcl')]:
        (tmp_path / entry).touch()
        monkeypatch.setenv(var, str(tmp_path))
    picker.configure_tk_libraries()
    assert os.environ['TCL_LIBRARY'] == str(tmp_path)
    assert os.environ['TK_LIBRARY'] == str(tmp_path)


@pytest.mark.parametrize('selection', [(), ('F:/新录像/第一段.mkv', 'F:/新录像/第二段.mp4'), None])
def test_selection_cancel_and_dialog_error_always_destroy_root(monkeypatch, selection):
    calls = []

    class Root:
        def withdraw(self): pass
        def attributes(self, *args): pass
        def destroy(self): calls.append('destroyed')

    def choose(**kwargs):
        if selection is None:
            raise RuntimeError('无法打开对话框')
        return selection

    monkeypatch.setattr(picker, 'configure_tk_libraries', lambda: None)
    monkeypatch.setattr(picker.tk, 'Tk', Root)
    monkeypatch.setattr(picker.filedialog, 'askopenfilenames', choose)
    if selection is None:
        with pytest.raises(RuntimeError):
            picker.select_files()
    else:
        assert picker.select_files() == list(selection)
    assert calls == ['destroyed']
