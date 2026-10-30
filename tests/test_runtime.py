"""runtime 目录定位测试：工作区跟随程序所在位置，不进用户目录。"""

from __future__ import annotations

import sys

from drpaper.runtime import ensure_workspace_dir, user_resource_dir, workspace_dir


def test_workspace_dir_frozen_at_exe_side(monkeypatch, tmp_path):
    exe = tmp_path / "DrPaper.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert workspace_dir() == tmp_path / ".drpaper"


def test_workspace_dir_source_at_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    assert workspace_dir() == tmp_path / ".drpaper"


def test_ensure_workspace_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    path = ensure_workspace_dir()
    assert path.is_dir()
    # 幂等：重复调用不报错
    assert ensure_workspace_dir() == path


def test_user_resource_dir_frozen_shares_exe_side(monkeypatch, tmp_path):
    exe = tmp_path / "DrPaper.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert user_resource_dir() == tmp_path
