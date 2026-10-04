from importlib import import_module
from pathlib import Path


def test_kronika_package_resolves_from_src_layout() -> None:
    module = import_module("kronika")

    module_path = Path(module.__file__).resolve()
    expected_package_directory = (
        Path(__file__).resolve().parents[2] / "src" / "kronika"
    )

    assert module_path.parent == expected_package_directory


def test_capture_package_resolves_from_src_layout() -> None:
    module = import_module("kronika_capture")
    expected_package_directory = (
        Path(__file__).resolve().parents[2] / "src" / "kronika_capture"
    )
    assert Path(module.__file__).resolve().parent == expected_package_directory
