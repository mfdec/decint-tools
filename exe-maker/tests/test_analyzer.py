from pathlib import Path

from decint_exe_maker import analyzer

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_gui_example():
    rep = analyzer.analyze(EXAMPLES / "hello_gui")
    assert rep.entry == "main.py"
    assert rep.app_type == "gui" and rep.gui_toolkit == "tk"
    assert rep.third_party == []
    assert "assets/greeting.json" in rep.data_files
    assert rep.suggested_name == "Hello Gui"
    assert analyzer.entry_module_name(rep.project_dir, "main.py") == ("main", [])


def test_console_example():
    rep = analyzer.analyze(EXAMPLES / "hello_console")
    assert rep.entry == "app.py"
    assert rep.app_type == "console"
    assert "helpers" in rep.local_modules
    assert rep.third_party == []


def test_third_party_and_pip_names(tmp_path):
    (tmp_path / "run.py").write_text("import PIL, cv2, yaml, requests, customtkinter\nfrom sklearn import svm\n"
                                     "if __name__ == '__main__':\n    pass\n")
    (tmp_path / "requirements.txt").write_text("requests>=2\n# comment\n")
    rep = analyzer.analyze(tmp_path)
    assert set(rep.third_party) == {"PIL", "cv2", "yaml", "requests", "customtkinter", "sklearn"}
    assert set(rep.pip_requirements) == {"pillow", "opencv-python", "pyyaml", "requests", "customtkinter", "scikit-learn"}
    assert "customtkinter" in rep.collect_all
    assert "PIL._tkinter_finder" in rep.hidden_imports
    assert rep.requirements_txt == ["requests>=2"]
    assert rep.app_type == "gui"


def test_nested_package_entry(tmp_path):
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text("from . import core\nif __name__ == '__main__':\n    core.run()\n")
    (pkg / "core.py").write_text("def run(): print('hi')\n")
    rep = analyzer.analyze(tmp_path)
    assert rep.entry == "src/myapp/__main__.py"
    mod, extra = analyzer.entry_module_name(tmp_path, rep.entry)
    assert mod == "myapp.__main__"
    assert extra == [str(tmp_path / "src")]
